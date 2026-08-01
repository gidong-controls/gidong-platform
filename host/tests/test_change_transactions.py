from __future__ import annotations

import base64
import copy
import hashlib
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import FakeMCP, authenticate_client
from fastapi.testclient import TestClient
from pydantic import ValidationError

from controlstudio_host.agent_runtime import AgentRunOutput
from controlstudio_host.api_models import (
    REQUIRED_CHANGE_COVERAGE_AREAS,
    ChangePlanV1,
)
from controlstudio_host.app import create_app
from controlstudio_host.change_execution import (
    ChangeApplyResult,
    ChangeExecutionFailedSafely,
    ChangeRecoveryTarget,
    ChangeVerificationResult,
    MCPChangeExecutionService,
    calculate_plan_digest,
)
from controlstudio_host.config import HostConfig
from controlstudio_host.mcp_client import MCPGateway
from controlstudio_host.model_connection import (
    MemorySecretStore,
    ModelConnectionRepository,
)
from controlstudio_host.models import (
    ApprovalDecision,
    ChangeRiskLevel,
    ChangeTargetIdentity,
    ChangeTransactionState,
    RunState,
)
from controlstudio_host.store import (
    ApprovalExpiredConflict,
    ChangeStoreConflict,
    HostStore,
    MutationLeaseConflict,
)

TEST_LAUNCH_TOKEN_VALUE = "test-launch-token-that-is-long-enough"
TEST_MODEL_API_KEY_VALUE = "test-key"


class VerifiedMCP(FakeMCP):
    async def call_read_tool(self, name: str, arguments: dict) -> dict:
        result = await super().call_read_tool(name, arguments)
        if name == "xg5000.attach":
            result["metadata"]["project_identity_verified_against_selected_pid"] = True
        return result


class SuccessfulChangeExecutor:
    def __init__(self) -> None:
        self.states: list[ChangeTransactionState] = []

    async def preflight(self, transaction) -> None:
        assert transaction.target.engineering_session_id == "session-test"

    async def apply(self, transaction) -> ChangeApplyResult:
        self.states.append(transaction.state)
        return ChangeApplyResult(evidence={"readback": "applied"})

    async def verify(
        self,
        transaction,
        apply_result,
    ) -> ChangeVerificationResult:
        assert apply_result.evidence == {"readback": "applied"}
        self.states.append(transaction.state)
        return ChangeVerificationResult(
            state=ChangeTransactionState.COMPLETED,
            evidence={"native_diff": "verified"},
        )

    async def reconcile(self, transaction, recovery_target) -> ChangeVerificationResult:
        assert transaction.state is ChangeTransactionState.QUARANTINED
        assert recovery_target.pid == 1234
        assert recovery_target.hwnd == 5678
        return ChangeVerificationResult(
            state=ChangeTransactionState.ROLLED_BACK,
            evidence={"native_status": "ROLLED_BACK_SAVED"},
        )


class FileMutatingSuccessfulChangeExecutor(SuccessfulChangeExecutor):
    async def apply(self, transaction) -> ChangeApplyResult:
        Path(transaction.target.project_path).write_bytes(b"saved-project-after-change")
        return await super().apply(transaction)


_CHANGE_COVERAGE_EVIDENCE_TOOLS = {
    "target_identity": "xg5000.session_status",
    "value_semantics": "program.read_ld",
    "direct_impact": "device.references",
    "indirect_impact": "program.logic_graph",
    "modes": "program.state_model",
    "safety": "program.state_model",
    "validation": "project.snapshot",
    "rollback": "project.snapshot",
}


def _agent_evidence_id(
    *,
    conversation_id: str,
    run_id: str,
    tool_name: str,
    tool_call_id: str,
) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            (
                "controlstudio-agent-evidence:"
                f"{conversation_id}:{run_id}:{tool_name}:{tool_call_id}"
            ),
        )
    )


def _complete_change_coverage(
    evidence_ref: str | dict[str, str] | None = None,
) -> list[dict]:
    def evidence_for(area: str) -> str:
        if isinstance(evidence_ref, dict):
            return evidence_ref[area]
        return evidence_ref or f"evidence:test:{area}"

    return [
        {
            "area": area,
            "status": (
                "not_applicable_with_reason"
                if area in {"runtime", "external_integrations"}
                else "confirmed"
            ),
            "summary": (
                "This saved offline revision does not execute or observe runtime state."
                if area == "runtime"
                else (
                    "No external integration is part of this exact operand revision."
                    if area == "external_integrations"
                    else f"{area} was checked for this exact operand revision."
                )
            ),
            "evidence_refs": (
                []
                if area in {"runtime", "external_integrations"}
                else [evidence_for(area)]
            ),
        }
        for area in REQUIRED_CHANGE_COVERAGE_AREAS
    ]


def _complete_change_contract(
    *,
    evidence_ref: str | dict[str, str] | None = None,
    request_scope: str | None = "permanent_offline_revision",
) -> dict:
    contract = {
        "change_mode": "offline_working_copy_revision",
        "assumptions": ["The selected native payload is the intended offline working copy."],
        "blocking_unknowns": [],
        "alternatives": ["Leave the working copy unchanged and collect more runtime evidence."],
        "acceptance_criteria": ["Only the approved operand changes and all native checks pass."],
        "observation_signals": [
            "Manually observe the affected output path before field acceptance."
        ],
        "stop_conditions": ["Stop if the structural diff contains any unapproved change."],
        "coverage": _complete_change_coverage(evidence_ref),
    }
    if request_scope is not None:
        contract["request_scope"] = request_scope
    return contract


def _seed_change_run_evidence(
    store: HostStore,
    *,
    conversation_id: str,
) -> tuple[str, dict[str, str]]:
    run = store.create_run(
        conversation_id=conversation_id,
        state=RunState.COMPLETED,
        operation_id=f"test-evidence:{uuid.uuid4()}",
    )
    assert run is not None
    evidence_by_tool: dict[str, str] = {}
    for tool_name in dict.fromkeys(_CHANGE_COVERAGE_EVIDENCE_TOOLS.values()):
        tool_call_id = f"test-evidence-{tool_name}"
        evidence_id = _agent_evidence_id(
            conversation_id=conversation_id,
            run_id=run.id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
        )
        store.append_event(
            event_type="evidence.added",
            conversation_id=conversation_id,
            run_id=run.id,
            payload={
                "evidenceId": evidence_id,
                "title": f"Test evidence from {tool_name}",
                "kind": "artifact",
                "toolName": tool_name,
                "summary": "Server-authored evidence for the exact test run.",
                "confidence": "confirmed",
            },
        )
        evidence_by_tool[tool_name] = evidence_id
    return run.id, {
        area: evidence_by_tool[tool_name]
        for area, tool_name in _CHANGE_COVERAGE_EVIDENCE_TOOLS.items()
    }


class PlanningRuntime:
    def __init__(self, *, evidence_override: str | None = None) -> None:
        self._evidence_override = evidence_override

    def bind_change_planner(self, callback) -> None:
        self._callback = callback

    async def run(self, **kwargs) -> AgentRunOutput:
        evidence_by_tool: dict[str, str] = {}
        for tool_name in dict.fromkeys(_CHANGE_COVERAGE_EVIDENCE_TOOLS.values()):
            tool_call_id = f"planning-runtime-{tool_name}"
            await kwargs["on_tool_event"](
                "tool.started",
                {
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                },
            )
            await kwargs["on_tool_event"](
                "tool.completed",
                {
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                },
            )
            evidence_by_tool[tool_name] = _agent_evidence_id(
                conversation_id=kwargs["conversation_id"],
                run_id=kwargs["run_id"],
                tool_name=tool_name,
                tool_call_id=tool_call_id,
            )
        evidence_ref: str | dict[str, str] = self._evidence_override or {
            area: evidence_by_tool[tool_name]
            for area, tool_name in _CHANGE_COVERAGE_EVIDENCE_TOOLS.items()
        }
        proposal = await self._callback(
            intent="Replace one operand",
            risk_level="high",
            **_complete_change_contract(
                evidence_ref=evidence_ref,
                request_scope=None,
            ),
            target={"programId": "program:MAIN", "tokenOffset": 14},
            current={"value": "M0001"},
            proposed={"value": "M0002"},
            impact=["Y0001 enable path"],
            risks=["Sequence timing changes"],
            verification_scope="offline_native_check_only",
            validation=[
                "structural_diff",
                "xg5000_program_check",
                "project_save",
            ],
            manual_follow_up=["Reopen and compare the saved project before field use."],
            rollback=["Restore pinned backup", "Save and re-read"],
        )
        return AgentRunOutput(
            content=f"승인 대기: {proposal['transaction_id']}",
            model="fake-planning-model",
            input_tokens=10,
            output_tokens=5,
        )


class PlannerBindingObservationRuntime:
    def __init__(self) -> None:
        self.planner_bound = False
        self.trusted_context = None

    def bind_change_planner(self, _callback) -> None:
        self.planner_bound = True

    async def run(self, **kwargs) -> AgentRunOutput:
        self.trusted_context = kwargs.get("trusted_context")
        return AgentRunOutput(
            content=f"planner_bound={self.planner_bound}",
            model="fake-planning-model",
            input_tokens=2,
            output_tokens=2,
        )


class RecordingChangeGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.signed_claims: list[dict] = []
        self._host_transaction_id: str | None = None
        self._signer = MCPGateway(
            authorization_secret=base64.urlsafe_b64encode(b"x" * 48).decode("ascii").rstrip("=")
        )

    def sign_mutation_authorization(self, claims: dict) -> dict:
        self.signed_claims.append(dict(claims))
        return self._signer.sign_mutation_authorization(claims)

    async def call_change_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, arguments))
        if name == "project.snapshot":
            self._host_transaction_id = str(arguments["host_transaction_id"])
        target = _target()
        project_identity_digest = MCPChangeExecutionService._project_identity_digest(target)
        plan = {
            "plan_id": "mcp-plan-1",
            "session_id": "session-verified",
            "transaction_id": "mcp-transaction-1",
            "workspace_id": _target().workspace_id,
            "project_identity_digest": project_identity_digest,
            "snapshot_id": "snapshot-1",
            "backup_id": "backup-1",
            "program_id": "program:MAIN",
            "token_offset": 14,
            "expected": "M0001",
            "replacement": "M0002",
            "payload_sha256_before": "1" * 64,
            "payload_sha256_planned": "2" * 64,
            "project_hash": "a" * 64,
        }
        plan["plan_digest"] = MCPChangeExecutionService._mcp_plan_digest(plan)

        def checkpoint(state: str, project_hash: str) -> dict:
            terminal = state in {
                "COMMITTED",
                "ROLLED_BACK_SAVED",
                "ABORTED_NO_MUTATION",
            }
            return {
                "schema": "controlstudio-change-transaction-checkpoint/v1",
                "host_transaction_id": self._host_transaction_id,
                "transaction_id": "mcp-transaction-1",
                "snapshot_id": "snapshot-1",
                "backup_id": "backup-1",
                "plan_id": "mcp-plan-1",
                "plan_digest": plan["plan_digest"],
                "workspace_id": target.workspace_id,
                "project_identity_digest": project_identity_digest,
                "active_session_id": "session-verified",
                "pid": 1234,
                "hwnd": 5678,
                "project_path": target.project_path,
                "current_project_hash": project_hash,
                "state": state,
                "program_id": "program:MAIN",
                "token_offset": 14,
                "expected": "M0001",
                "replacement": "M0002",
                "mutation_possible": not terminal,
                "recovery_action": ("already_terminal" if terminal else "rollback_required"),
                "terminal": terminal,
                "lease_status": "released" if terminal else "live",
            }

        common = {
            "ok": True,
            "verified": True,
            "session_id": "session-verified",
            "transaction_id": "mcp-transaction-1",
        }
        results = {
            "project.snapshot": {
                "snapshot_id": "snapshot-1",
                "transaction_id": "mcp-transaction-1",
                "programs": [
                    {
                        "program_id": "program:MAIN",
                        "payload_sha256": "1" * 64,
                    }
                ],
            },
            "project.backup": {"backup_id": "backup-1"},
            "program.plan_change": {"plan": plan},
            "program.apply_ld_patch": {"transaction_state": "APPLIED"},
            "project.diff": {
                **common,
                "operation": "project.diff",
                "transaction_state": "DIFF_VERIFIED",
                "transaction_checkpoint": checkpoint("DIFF_VERIFIED", "a" * 64),
                "plan_id": "mcp-plan-1",
                "matches_plan": True,
                "matches_token_plan": True,
                "matches_native_plan": True,
                "project_unchanged": True,
            },
            "project.validate_change": {
                **common,
                "operation": "project.validate_change",
                "transaction_state": "CHECKED",
                "transaction_checkpoint": checkpoint("CHECKED", "a" * 64),
                "plan_id": "mcp-plan-1",
                "matches_native_plan": True,
                "status": "validated",
                "rollback": None,
                "project_unchanged": True,
                "program_check": {"ok": True, "verified": True},
            },
            "project.commit_save": {
                **common,
                "operation": "project.commit_save",
                "transaction_state": "SAVED",
                "project_hash": "e" * 64,
                "project_path": target.project_path,
                "transaction_checkpoint": checkpoint("SAVED", "e" * 64),
            },
            "change.transaction_status": {
                **common,
                "operation": "change.transaction_status",
                "transaction": {
                    "transaction_id": "mcp-transaction-1",
                    "state": "SAVED",
                    "active_session_id": "session-verified",
                    "active_pid": 1234,
                    "active_hwnd": 5678,
                    "project_path": target.project_path,
                    "current_project_hash": "e" * 64,
                },
                "transaction_checkpoint": checkpoint("SAVED", "e" * 64),
                "events": [],
            },
            "change.commit": {
                **common,
                "operation": "change.commit",
                "transaction_state": "COMMITTED",
                "project_hash": "e" * 64,
                "project_unchanged": True,
                "transaction_checkpoint": checkpoint("COMMITTED", "e" * 64),
            },
        }
        return results[name]


class TamperingChangeGateway(RecordingChangeGateway):
    def __init__(self, field: str, value) -> None:
        super().__init__()
        self._field = field
        self._value = value

    async def call_change_tool(self, name: str, arguments: dict) -> dict:
        result = await super().call_change_tool(name, arguments)
        if name == "program.plan_change":
            result["plan"][self._field] = self._value
            if self._field != "plan_digest":
                result["plan"]["plan_digest"] = MCPChangeExecutionService._mcp_plan_digest(
                    result["plan"]
                )
        return result


class RestartRecoveryGateway:
    """Stateful MCP boundary that rejects use of the dead engineering session."""

    def __init__(
        self,
        *,
        transaction,
        native_state: str,
        current_project_hash: str,
        checkpoint_overrides: dict | None = None,
    ) -> None:
        self.transaction = transaction
        self.native_state = native_state
        self.current_project_hash = current_project_hash
        self.native_transaction_id = "33333333-3333-4333-8333-333333333333"
        self.recovery_session_id = "session-recovery-only"
        self.recovery_attached = False
        self.calls: list[tuple[str, dict]] = []
        self.checkpoint_overrides = checkpoint_overrides or {}

    def _checkpoint(self) -> dict:
        if self.native_state in {
            "SNAPSHOT_CREATED",
            "BACKUP_CREATED",
            "PLANNED",
        }:
            recovery_action = "abort_safe"
        elif self.native_state in {"APPLIED", "QUARANTINED"}:
            recovery_action = "rollback_required"
        else:
            recovery_action = "already_terminal"
        terminal = self.native_state in {
            "ABORTED_NO_MUTATION",
            "COMMITTED",
            "ROLLED_BACK_SAVED",
        }
        checkpoint = {
            "schema": "controlstudio-change-transaction-checkpoint/v1",
            "host_transaction_id": self.transaction.id,
            "transaction_id": self.native_transaction_id,
            "snapshot_id": "snapshot-restart",
            "backup_id": ("backup-restart" if self.native_state != "SNAPSHOT_CREATED" else None),
            "plan_id": (
                "plan-restart"
                if self.native_state not in {"SNAPSHOT_CREATED", "BACKUP_CREATED"}
                else None
            ),
            "plan_digest": (
                "b" * 64
                if self.native_state not in {"SNAPSHOT_CREATED", "BACKUP_CREATED"}
                else None
            ),
            "workspace_id": self.transaction.target.workspace_id,
            "project_identity_digest": (
                MCPChangeExecutionService._project_identity_digest(self.transaction.target)
            ),
            "active_session_id": (
                self.recovery_session_id if self.recovery_attached else "dead-session"
            ),
            "pid": 4321 if self.recovery_attached else 1234,
            "hwnd": 8765 if self.recovery_attached else 5678,
            "project_path": self.transaction.target.project_path,
            "current_project_hash": self.current_project_hash,
            "state": self.native_state,
            "program_id": "program:MAIN",
            "token_offset": 14,
            "expected": "M0001",
            "replacement": "M0002",
            "mutation_possible": recovery_action == "rollback_required",
            "recovery_action": recovery_action,
            "terminal": terminal,
            "lease_status": "released" if terminal else "abandoned",
        }
        checkpoint.update(self.checkpoint_overrides)
        return checkpoint

    async def call_recovery_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, dict(arguments)))
        if name == "change.transaction_lookup":
            assert arguments == {"host_transaction_id": self.transaction.id}
            return {"transaction_checkpoint": self._checkpoint()}
        if name == "xg5000.attach":
            assert arguments["recover_transaction_id"] == self.native_transaction_id
            assert arguments["access_profile"] == "analysis"
            assert arguments["pid"] == 4321
            assert arguments["hwnd"] == 8765
            assert arguments["workspace_id"] == self.transaction.target.workspace_id
            assert arguments["project_path"] == self.transaction.target.project_path
            assert arguments["project_hash"] == self.current_project_hash
            if self._checkpoint()["plan_digest"] is not None:
                assert (
                    arguments["recover_expected_plan_digest"] == self._checkpoint()["plan_digest"]
                )
            else:
                assert "recover_expected_plan_digest" not in arguments
            self.recovery_attached = True
            return {
                "session_id": self.recovery_session_id,
                "pid": 4321,
                "hwnd": 8765,
                "project_path": self.transaction.target.project_path,
                "project_hash": self.current_project_hash,
                "plc_policy": (
                    "offline_abort_recovery_only"
                    if self._checkpoint()["recovery_action"] == "abort_safe"
                    else "offline_rollback_recovery_only"
                ),
                "recovery": self._checkpoint(),
            }
        raise AssertionError(f"unexpected recovery tool: {name}")

    async def call_change_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, dict(arguments)))
        assert self.recovery_attached
        assert arguments["session_id"] == self.recovery_session_id
        assert arguments["transaction_id"] == self.native_transaction_id
        if name == "change.transaction_status":
            return {
                "transaction_checkpoint": self._checkpoint(),
                "events": [],
            }
        if name == "change.abort":
            assert arguments["expected_transaction_state"] == self.native_state
            state_before = self.native_state
            self.native_state = "ABORTED_NO_MUTATION"
            return {
                "state_before": state_before,
                "state_after": self.native_state,
                "native_mutation_performed": False,
                "lease_released": True,
                "transaction_checkpoint": self._checkpoint(),
            }
        if name == "project.rollback_change":
            assert arguments["plan_id"] == "plan-restart"
            self.native_state = "ROLLED_BACK"
            return {"transaction_state": self.native_state}
        if name == "project.commit_save":
            self.native_state = "ROLLED_BACK_SAVED"
            return {
                "transaction_state": self.native_state,
                "project_hash": self.current_project_hash,
            }
        raise AssertionError(f"unexpected change tool: {name}")


def _target() -> ChangeTargetIdentity:
    return ChangeTargetIdentity(
        workspace_id="11111111-1111-4111-8111-111111111111",
        revision_id="22222222-2222-4222-8222-222222222222",
        artifact_hash="a" * 64,
        source_artifact_hash="a" * 64,
        project_path="C:\\workspaces\\working\\machine.xgwx",
        vendor="ls",
        engineering_system="XG5000",
        engineering_session_id="session-verified",
        engineering_process_id=1234,
        engineering_window_handle=5678,
    )


def _plan(
    evidence_ref: str | dict[str, str] | None = None,
) -> dict:
    return {
        **_complete_change_contract(evidence_ref=evidence_ref),
        "target": {"programId": "program:MAIN", "tokenOffset": 14},
        "current": {"value": "M0001"},
        "proposed": {"value": "M0002"},
        "impact": ["Y0001 enable path"],
        "risks": ["Sequence timing changes"],
        "verification_scope": "offline_native_check_only",
        "validation": [
            "structural_diff",
            "xg5000_program_check",
            "project_save",
        ],
        "manual_follow_up": [
            "Reopen the project and compare the saved program.",
            "Run the approved XG-SIM scenario.",
        ],
        "rollback": ["Restore pinned backup", "Save and re-read native payload"],
    }


def _create(
    store: HostStore,
    *,
    operation_id: str,
    expires_at: datetime | None = None,
) -> tuple:
    target = _target()
    plan = _plan()
    digest = calculate_plan_digest(
        target=target,
        risk_level=ChangeRiskLevel.HIGH,
        intent="Replace one ladder operand",
        plan=plan,
    )
    return store.create_change_transaction(
        operation_id=operation_id,
        target=target,
        mutation_scope=f"workspace:{target.workspace_id}",
        risk_level=ChangeRiskLevel.HIGH,
        intent="Replace one ladder operand",
        plan=plan,
        plan_digest=digest,
        requested_by_actor_id="browser:actor-one",
        approval_expires_at=expires_at or datetime.now(timezone.utc) + timedelta(minutes=15),
    )


def test_change_contract_is_idempotent_and_approval_is_one_time(
    tmp_path: Path,
) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    transaction, created = _create(store, operation_id="change-operation-0001")
    duplicate, duplicate_created = _create(
        store,
        operation_id="change-operation-0001",
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate.id == transaction.id
    assert transaction.state is ChangeTransactionState.PENDING_APPROVAL
    assert len(transaction.plan_digest) == 64

    approved, decision_recorded = store.decide_change_transaction(
        transaction_id=transaction.id,
        decision=ApprovalDecision.APPROVE,
        expected_plan_digest=transaction.plan_digest,
        actor_id="browser:approver",
    )
    assert approved is not None
    assert decision_recorded is True
    assert approved.state is ChangeTransactionState.APPROVED
    assert approved.approval is not None
    assert approved.approval.actor_id == "browser:approver"

    idempotent, decision_recorded = store.decide_change_transaction(
        transaction_id=transaction.id,
        decision=ApprovalDecision.APPROVE,
        expected_plan_digest=transaction.plan_digest,
        actor_id="browser:approver",
    )
    assert idempotent is not None
    assert decision_recorded is False

    with pytest.raises(ChangeStoreConflict, match="one-time decision"):
        store.decide_change_transaction(
            transaction_id=transaction.id,
            decision=ApprovalDecision.REJECT,
            expected_plan_digest=transaction.plan_digest,
            actor_id="browser:approver",
        )

    assert [event.event_type for event in store.list_events()] == [
        "change.transaction_created",
        "approval.decided",
    ]


def test_expired_approval_cannot_be_reused(tmp_path: Path) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    transaction, _ = _create(
        store,
        operation_id="change-operation-expired",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    expired, recorded = store.decide_change_transaction(
        transaction_id=transaction.id,
        decision=ApprovalDecision.APPROVE,
        expected_plan_digest=transaction.plan_digest,
        actor_id="browser:late-approver",
    )

    assert recorded is True
    assert expired is not None
    assert expired.state is ChangeTransactionState.EXPIRED
    assert expired.approval is None
    with pytest.raises(ChangeStoreConflict, match="state EXPIRED"):
        store.decide_change_transaction(
            transaction_id=transaction.id,
            decision=ApprovalDecision.APPROVE,
            expected_plan_digest=transaction.plan_digest,
            actor_id="browser:late-approver",
        )


def test_approved_contract_expiry_is_enforced_atomically_at_execution(
    tmp_path: Path,
) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    decision_time = datetime.now(timezone.utc)
    transaction, _ = _create(
        store,
        operation_id="change-operation-approved-then-expired",
        expires_at=decision_time + timedelta(seconds=30),
    )
    approved, _ = store.decide_change_transaction(
        transaction_id=transaction.id,
        decision=ApprovalDecision.APPROVE,
        expected_plan_digest=transaction.plan_digest,
        actor_id="browser:approver",
        decided_at=decision_time,
    )
    assert approved is not None
    assert approved.state is ChangeTransactionState.APPROVED

    with pytest.raises(ApprovalExpiredConflict, match="expired"):
        store.begin_change_execution(
            transaction_id=transaction.id,
            expected_plan_digest=transaction.plan_digest,
            started_at=decision_time + timedelta(seconds=31),
        )

    expired = store.get_change_transaction(transaction.id)
    assert expired is not None
    assert expired.state is ChangeTransactionState.EXPIRED
    assert expired.error_code == "approval_expired"
    assert expired.execution_started_at is None
    assert store.active_mutation_lease(transaction.mutation_scope) is None
    assert [event.event_type for event in store.list_events()][-1] == ("approval.expired")


def test_mutation_lease_blocks_duplicate_target_until_verified_terminal_state(
    tmp_path: Path,
) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    first, _ = _create(store, operation_id="change-operation-first")
    second, _ = _create(store, operation_id="change-operation-second")
    for transaction in (first, second):
        approved, _ = store.decide_change_transaction(
            transaction_id=transaction.id,
            decision=ApprovalDecision.APPROVE,
            expected_plan_digest=transaction.plan_digest,
            actor_id="browser:approver",
        )
        assert approved is not None

    executing = store.begin_change_execution(
        transaction_id=first.id,
        expected_plan_digest=first.plan_digest,
    )
    assert executing is not None
    assert executing.state is ChangeTransactionState.EXECUTING
    with pytest.raises(MutationLeaseConflict) as conflict:
        store.begin_change_execution(
            transaction_id=second.id,
            expected_plan_digest=second.plan_digest,
        )
    assert conflict.value.transaction_id == first.id

    verifying = store.mark_change_verifying(
        transaction_id=first.id,
        apply_evidence={"native_payload_hash": "b" * 64},
    )
    assert verifying is not None
    assert verifying.state is ChangeTransactionState.VERIFYING
    completed = store.finish_change_execution(
        transaction_id=first.id,
        state=ChangeTransactionState.COMPLETED,
        verification_evidence={"round_trip": "verified"},
    )
    assert completed is not None
    assert completed.state is ChangeTransactionState.COMPLETED
    assert store.active_mutation_lease(first.mutation_scope) is None

    second_execution = store.begin_change_execution(
        transaction_id=second.id,
        expected_plan_digest=second.plan_digest,
    )
    assert second_execution is not None
    assert second_execution.state is ChangeTransactionState.EXECUTING


def test_terminal_change_receipt_acknowledgement_is_durable_and_idempotent(
    tmp_path: Path,
) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    terminal, _ = _create(store, operation_id="change-receipt-terminal")
    rejected, _ = store.decide_change_transaction(
        transaction_id=terminal.id,
        decision=ApprovalDecision.REJECT,
        expected_plan_digest=terminal.plan_digest,
        actor_id="browser:reviewer",
    )
    assert rejected is not None
    assert rejected.state is ChangeTransactionState.REJECTED

    acknowledged, recorded = store.acknowledge_change_receipt(
        transaction_id=terminal.id,
        actor_id="browser:reviewer",
    )
    assert acknowledged is not None
    assert recorded is True
    assert acknowledged.receipt_acknowledged_at is not None
    assert acknowledged.receipt_acknowledged_by == "browser:reviewer"

    restored = HostStore(store.database_path).get_change_transaction(terminal.id)
    assert restored is not None
    assert restored.receipt_acknowledged_at == acknowledged.receipt_acknowledged_at
    repeated, repeated_recorded = store.acknowledge_change_receipt(
        transaction_id=terminal.id,
        actor_id="browser:reviewer",
    )
    assert repeated is not None
    assert repeated_recorded is False
    assert [
        event.event_type
        for event in store.list_events()
        if event.event_type == "change.receipt_acknowledged"
    ] == ["change.receipt_acknowledged"]

    pending, _ = _create(store, operation_id="change-receipt-pending")
    with pytest.raises(ChangeStoreConflict, match="terminal"):
        store.acknowledge_change_receipt(
            transaction_id=pending.id,
            actor_id="browser:reviewer",
        )


def test_restart_quarantines_uncertain_change_and_retains_lease(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "host.sqlite3"
    store = HostStore(database_path)
    transaction, _ = _create(store, operation_id="change-operation-interrupted")
    store.decide_change_transaction(
        transaction_id=transaction.id,
        decision=ApprovalDecision.APPROVE,
        expected_plan_digest=transaction.plan_digest,
        actor_id="browser:approver",
    )
    store.begin_change_execution(
        transaction_id=transaction.id,
        expected_plan_digest=transaction.plan_digest,
    )

    restarted = HostStore(database_path)
    assert restarted.reconcile_interrupted_change_transactions() == 1
    quarantined = restarted.get_change_transaction(transaction.id)
    assert quarantined is not None
    assert quarantined.state is ChangeTransactionState.QUARANTINED
    lease = restarted.active_mutation_lease(transaction.mutation_scope)
    assert lease is not None
    assert lease["transaction_id"] == transaction.id
    resolved = restarted.resolve_quarantined_change(
        transaction_id=transaction.id,
        state=ChangeTransactionState.ROLLED_BACK,
        evidence={"native_status": "ROLLED_BACK_SAVED"},
        actor_id="browser:recovery-operator",
        error_code="explicit_recovery_rolled_back",
    )
    assert resolved is not None
    assert resolved.state is ChangeTransactionState.ROLLED_BACK
    assert restarted.active_mutation_lease(transaction.mutation_scope) is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("native_state", "expected_error_code", "expected_change_tool"),
    [
        (
            "SNAPSHOT_CREATED",
            "explicit_recovery_aborted_before_mutation",
            "change.abort",
        ),
        (
            "APPLIED",
            "explicit_recovery_rolled_back",
            "project.rollback_change",
        ),
    ],
)
async def test_restart_recovery_uses_new_recovery_only_session_and_releases_lease(
    tmp_path: Path,
    native_state: str,
    expected_error_code: str,
    expected_change_tool: str,
) -> None:
    project_path = (tmp_path / "machine.xgwx").resolve()
    project_path.write_bytes(b"native-project-before-or-after-interruption")
    project_hash = hashlib.sha256(project_path.read_bytes()).hexdigest()
    database_path = tmp_path / f"host-{native_state}.sqlite3"
    store = HostStore(database_path)
    target = ChangeTargetIdentity(
        workspace_id="11111111-1111-4111-8111-111111111111",
        revision_id="22222222-2222-4222-8222-222222222222",
        artifact_hash=project_hash,
        source_artifact_hash=project_hash,
        project_path=str(project_path),
        vendor="ls",
        engineering_system="XG5000",
        engineering_session_id="dead-session",
        engineering_process_id=1234,
        engineering_window_handle=5678,
    )
    plan = _plan()
    plan_digest = calculate_plan_digest(
        target=target,
        risk_level=ChangeRiskLevel.HIGH,
        intent="Replace one ladder operand",
        plan=plan,
    )
    transaction, _ = store.create_change_transaction(
        operation_id=f"restart-recovery-{native_state.casefold()}",
        target=target,
        mutation_scope=f"workspace:{target.workspace_id}",
        risk_level=ChangeRiskLevel.HIGH,
        intent="Replace one ladder operand",
        plan=plan,
        plan_digest=plan_digest,
        requested_by_actor_id="browser:requester",
        approval_expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    store.decide_change_transaction(
        transaction_id=transaction.id,
        decision=ApprovalDecision.APPROVE,
        expected_plan_digest=transaction.plan_digest,
        actor_id="browser:approver",
    )
    store.begin_change_execution(
        transaction_id=transaction.id,
        expected_plan_digest=transaction.plan_digest,
    )

    restarted = HostStore(database_path)
    assert restarted.reconcile_interrupted_change_transactions() == 1
    quarantined = restarted.get_change_transaction(transaction.id)
    assert quarantined is not None
    assert quarantined.state is ChangeTransactionState.QUARANTINED
    gateway = RestartRecoveryGateway(
        transaction=quarantined,
        native_state=native_state,
        current_project_hash=project_hash,
    )
    service = MCPChangeExecutionService(gateway)  # type: ignore[arg-type]

    outcome = await service.reconcile(
        quarantined,
        ChangeRecoveryTarget(pid=4321, hwnd=8765),
    )

    assert outcome.state is ChangeTransactionState.ROLLED_BACK
    assert outcome.error_code == expected_error_code
    assert expected_change_tool in [name for name, _ in gateway.calls]
    assert all(arguments.get("session_id") != "dead-session" for _name, arguments in gateway.calls)
    resolved = restarted.resolve_quarantined_change(
        transaction_id=transaction.id,
        state=outcome.state,
        evidence=outcome.evidence,
        actor_id="browser:recovery-operator",
        error_code=outcome.error_code,
        error_detail=outcome.error_detail,
    )
    assert resolved is not None
    assert resolved.state is ChangeTransactionState.ROLLED_BACK
    assert restarted.active_mutation_lease(transaction.mutation_scope) is None


@pytest.mark.anyio
async def test_recovery_rejects_substituted_native_plan_before_reattach(
    tmp_path: Path,
) -> None:
    project_path = (tmp_path / "machine.xgwx").resolve()
    project_path.write_bytes(b"native-project")
    project_hash = hashlib.sha256(project_path.read_bytes()).hexdigest()
    store = HostStore(tmp_path / "host.sqlite3")
    transaction, _ = _create(store, operation_id="recovery-plan-substitution")
    target = transaction.target.model_copy(
        update={
            "artifact_hash": project_hash,
            "source_artifact_hash": project_hash,
            "project_path": str(project_path),
        }
    )
    transaction = transaction.model_copy(update={"target": target})
    gateway = RestartRecoveryGateway(
        transaction=transaction,
        native_state="APPLIED",
        current_project_hash=project_hash,
        checkpoint_overrides={"replacement": "M9999"},
    )
    service = MCPChangeExecutionService(gateway)  # type: ignore[arg-type]

    outcome = await service.reconcile(
        transaction,
        ChangeRecoveryTarget(pid=4321, hwnd=8765),
    )

    assert outcome.state is ChangeTransactionState.QUARANTINED
    assert outcome.error_code == "recovery_identity_unavailable"
    assert [name for name, _ in gateway.calls] == ["change.transaction_lookup"]


def test_active_mutation_lease_blocks_workspace_and_xg5000_target_switch(
    tmp_path: Path,
) -> None:
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
        event_poll_interval_seconds=0.01,
        launch_token=TEST_LAUNCH_TOKEN_VALUE,
    )
    store = HostStore(config.database_path)
    transaction, _ = _create(
        store,
        operation_id="change-operation-target-lock",
    )
    store.decide_change_transaction(
        transaction_id=transaction.id,
        decision=ApprovalDecision.APPROVE,
        expected_plan_digest=transaction.plan_digest,
        actor_id="browser:approver",
    )
    store.begin_change_execution(
        transaction_id=transaction.id,
        expected_plan_digest=transaction.plan_digest,
    )

    with TestClient(
        create_app(config, store=store, mcp=VerifiedMCP()),
        base_url="http://localhost:8765",
    ) as client:
        authenticate_client(client, config)
        workspace_switch = client.post(
            "/api/v1/workspaces/open-local",
            json={
                "sourcePath": "C:\\projects\\other.xgwx",
                "openInXg5000": False,
            },
        )
        assert workspace_switch.status_code == 409
        attach_switch = client.post(
            "/api/v1/xg5000/attach",
            json={"pid": 9999, "hwnd": 8888},
        )
        assert attach_switch.status_code == 409


@pytest.mark.anyio
async def test_production_change_service_calls_host_owned_mcp_chain(
    tmp_path: Path,
) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    transaction, _ = _create(store, operation_id="change-operation-mcp-chain")
    approved, _ = store.decide_change_transaction(
        transaction_id=transaction.id,
        decision=ApprovalDecision.APPROVE,
        expected_plan_digest=transaction.plan_digest,
        actor_id="browser:approver",
    )
    assert approved is not None
    executing = store.begin_change_execution(
        transaction_id=approved.id,
        expected_plan_digest=approved.plan_digest,
    )
    assert executing is not None
    gateway = RecordingChangeGateway()
    service = MCPChangeExecutionService(  # type: ignore[arg-type]
        gateway,
        checkpoint_callback=store.record_native_change_checkpoint,
    )

    await service.preflight(executing)
    applied = await service.apply(executing)
    checkpointed = store.get_change_transaction(executing.id)
    assert checkpointed is not None
    assert checkpointed.result["native_checkpoint"]["stage"] == "PLANNED"
    assert checkpointed.result["native_checkpoint"]["mcp_transaction_id"] == "mcp-transaction-1"
    assert checkpointed.result["native_checkpoint"]["mcp_plan_id"] == "mcp-plan-1"
    verifying = store.mark_change_verifying(
        transaction_id=executing.id,
        apply_evidence=applied.evidence,
    )
    assert verifying is not None
    verified = await service.verify(verifying, applied)

    assert verified.state is ChangeTransactionState.COMPLETED
    names = [name for name, _arguments in gateway.calls]
    assert names == [
        "project.snapshot",
        "project.backup",
        "program.plan_change",
        "program.apply_ld_patch",
        "project.diff",
        "project.validate_change",
        "project.commit_save",
        "change.transaction_status",
        "change.commit",
    ]
    apply_arguments = gateway.calls[3][1]
    assert "confirm" not in apply_arguments
    assert apply_arguments["authorization"]["actor_id"] == "browser:approver"
    assert (
        apply_arguments["authorization"]["plan_digest"] == gateway.signed_claims[0]["plan_digest"]
    )
    assert apply_arguments["pid"] == 1234
    assert apply_arguments["hwnd"] == 5678
    commit_arguments = gateway.calls[-1][1]
    assert commit_arguments["project_hash"] == "e" * 64
    assert verified.evidence["verification_scope"] == "offline_native_check_only"
    assert verified.evidence["executed_checks"] == [
        "structural_diff",
        "xg5000_program_check",
        "project_save",
    ]
    assert "xg_sim" in verified.evidence["not_executed"]
    assert verified.evidence["manual_follow_up"] == [
        "Reopen the project and compare the saved program.",
        "Run the approved XG-SIM scenario.",
    ]
    assert all(
        name not in [called for called, _arguments in gateway.calls]
        for name in ("xg5000.open_project", "sim.start", "sim.run")
    )


@pytest.mark.anyio
async def test_host_rejects_success_shaped_but_semantically_wrong_mcp_results(
    tmp_path: Path,
) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    transaction, _ = _create(
        store,
        operation_id="change-operation-semantic-result-validation",
    )
    gateway = RecordingChangeGateway()
    gateway._host_transaction_id = transaction.id
    service = MCPChangeExecutionService(gateway)  # type: ignore[arg-type]

    diff = await gateway.call_change_tool("project.diff", {})
    wrong_diff = copy.deepcopy(diff)
    wrong_diff["matches_native_plan"] = False
    with pytest.raises(ValueError, match="exact approved native diff"):
        service._validate_diff_result(
            transaction=transaction,
            result=wrong_diff,
            mcp_transaction_id="mcp-transaction-1",
            mcp_plan_id="mcp-plan-1",
        )

    validation = await gateway.call_change_tool("project.validate_change", {})
    wrong_validation = copy.deepcopy(validation)
    wrong_validation["program_check"]["verified"] = False
    with pytest.raises(ValueError, match="program check"):
        service._validate_validation_result(
            transaction=transaction,
            result=wrong_validation,
            mcp_transaction_id="mcp-transaction-1",
            mcp_plan_id="mcp-plan-1",
        )

    save = await gateway.call_change_tool("project.commit_save", {})
    wrong_save = copy.deepcopy(save)
    wrong_save["transaction_checkpoint"]["current_project_hash"] = "f" * 64
    with pytest.raises(ValueError, match="current_project_hash"):
        service._validate_save_result(
            transaction=transaction,
            result=wrong_save,
            mcp_transaction_id="mcp-transaction-1",
            expected_state="SAVED",
        )

    status_result = await gateway.call_change_tool(
        "change.transaction_status",
        {},
    )
    wrong_status = copy.deepcopy(status_result)
    wrong_status["transaction"]["active_pid"] = 4321
    with pytest.raises(ValueError, match="active_pid"):
        service._validate_saved_status(
            transaction=transaction,
            status=wrong_status,
            mcp_transaction_id="mcp-transaction-1",
            expected_project_hash="e" * 64,
        )

    commit = await gateway.call_change_tool("change.commit", {})
    wrong_commit = copy.deepcopy(commit)
    wrong_commit["transaction_checkpoint"]["lease_status"] = "live"
    with pytest.raises(ValueError, match="terminal committed state"):
        service._validate_commit_result(
            transaction=transaction,
            result=wrong_commit,
            mcp_transaction_id="mcp-transaction-1",
            expected_project_hash="e" * 64,
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "substitution"),
    [
        ("program_id", "program:ATTACKER"),
        ("token_offset", 99),
        ("expected", "M9998"),
        ("replacement", "M9999"),
        ("session_id", "session-attacker"),
        ("transaction_id", "transaction-attacker"),
        ("workspace_id", "33333333-3333-4333-8333-333333333333"),
        ("project_identity_digest", "3" * 64),
        ("snapshot_id", "snapshot-attacker"),
        ("backup_id", "backup-attacker"),
        ("project_hash", "4" * 64),
        ("payload_sha256_before", "5" * 64),
        ("plan_digest", "6" * 64),
    ],
)
async def test_mcp_plan_substitution_is_rejected_before_host_signature(
    tmp_path: Path,
    field: str,
    substitution,
) -> None:
    store = HostStore(tmp_path / f"host-{field}.sqlite3")
    transaction, _ = _create(
        store,
        operation_id=f"change-tamper-{field.replace('_', '-')}",
    )
    approved, _ = store.decide_change_transaction(
        transaction_id=transaction.id,
        decision=ApprovalDecision.APPROVE,
        expected_plan_digest=transaction.plan_digest,
        actor_id="browser:approver",
    )
    assert approved is not None
    gateway = TamperingChangeGateway(field, substitution)
    service = MCPChangeExecutionService(gateway)  # type: ignore[arg-type]

    with pytest.raises(ChangeExecutionFailedSafely, match="mismatch|does not bind"):
        await service.apply(approved)

    assert gateway.signed_claims == []
    assert "program.apply_ld_patch" not in [name for name, _arguments in gateway.calls]


def test_unexecutable_reopen_or_simulation_promises_are_not_approval_validation() -> None:
    plan = _plan()
    plan["validation"] = [
        "structural_diff",
        "xg5000_program_check",
        "project_save",
        "xg_sim",
    ]

    with pytest.raises(ValidationError):
        ChangePlanV1.model_validate(plan)


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("empty_impact", "plan.impact"),
        ("empty_risks", "plan.risks"),
        ("empty_manual_follow_up", "plan.manualFollowUp"),
        ("empty_acceptance", "plan.acceptanceCriteria"),
        ("missing_change_mode", "missing explicit executable-contract fields"),
        ("missing_request_scope", "missing explicit executable-contract fields"),
        ("missing_blocking_unknowns", "missing explicit executable-contract fields"),
        ("missing_validation", "missing explicit executable-contract fields"),
        ("blocking_unknown", "plan.blockingUnknowns"),
        ("missing_coverage", "missing required areas"),
        ("confirmed_without_evidence", "requires evidenceRefs"),
        ("coverage_unknown", "unknown_blocking"),
        ("different_native_length", "equal UTF-16 byte length"),
    ],
)
def test_executable_change_contract_rejects_incomplete_or_unsafe_plans(
    case: str,
    message: str,
) -> None:
    plan_data = copy.deepcopy(_plan())
    if case == "empty_impact":
        plan_data["impact"] = []
    elif case == "empty_risks":
        plan_data["risks"] = []
    elif case == "empty_manual_follow_up":
        plan_data["manual_follow_up"] = []
    elif case == "empty_acceptance":
        plan_data["acceptance_criteria"] = []
    elif case == "missing_change_mode":
        del plan_data["change_mode"]
    elif case == "missing_request_scope":
        del plan_data["request_scope"]
    elif case == "missing_blocking_unknowns":
        del plan_data["blocking_unknowns"]
    elif case == "missing_validation":
        del plan_data["validation"]
    elif case == "blocking_unknown":
        plan_data["blocking_unknowns"] = ["Timer time base is not confirmed."]
    elif case == "missing_coverage":
        plan_data["coverage"] = plan_data["coverage"][:-1]
    elif case == "confirmed_without_evidence":
        plan_data["coverage"][0]["evidence_refs"] = []
    elif case == "coverage_unknown":
        plan_data["coverage"][0]["status"] = "unknown_blocking"
        plan_data["coverage"][0]["evidence_refs"] = []
    elif case == "different_native_length":
        plan_data["current"] = {"value": "K30"}
        plan_data["proposed"] = {"value": "K300"}
    else:  # pragma: no cover - protects the parametrized fixture
        raise AssertionError(f"unknown test case: {case}")

    plan = ChangePlanV1.model_validate(plan_data)
    with pytest.raises(ValueError, match=message):
        plan.validate_executable_contract()


def test_host_rejects_incomplete_change_contract_before_transaction_creation(
    tmp_path: Path,
) -> None:
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
        event_poll_interval_seconds=0.01,
        launch_token=TEST_LAUNCH_TOKEN_VALUE,
    )
    app = create_app(
        config,
        store=HostStore(config.database_path),
        mcp=VerifiedMCP(),
        change_executor=SuccessfulChangeExecutor(),
    )
    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        opened = client.post(
            "/api/v1/workspaces/open-local",
            json={
                "sourcePath": "C:\\projects\\machine.xgwx",
                "openInXg5000": True,
            },
        )
        assert opened.status_code == 200
        plan = _plan()
        plan["blocking_unknowns"] = ["The timer time base is not confirmed."]
        rejected = client.post(
            "/api/v1/change-transactions",
            json={
                "operationId": "change-blocked-contract-0001",
                "intent": "Increase a timer in the saved offline working copy",
                "riskLevel": "high",
                "plan": ChangePlanV1.model_validate(plan).model_dump(
                    mode="json",
                    by_alias=True,
                ),
                "approvalTtlSeconds": 900,
            },
        )

        assert rejected.status_code == 409
        assert "blockingUnknowns must be empty" in rejected.json()["detail"]
        assert client.get("/api/v1/change-transactions").json() == []


@pytest.mark.parametrize(
    "binding_case",
    ["neither", "conversation_only", "run_only"],
)
def test_executable_change_requires_exact_conversation_and_run_binding(
    tmp_path: Path,
    binding_case: str,
) -> None:
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
        event_poll_interval_seconds=0.01,
        launch_token=TEST_LAUNCH_TOKEN_VALUE,
    )
    store = HostStore(config.database_path)
    app = create_app(
        config,
        store=store,
        mcp=VerifiedMCP(),
        change_executor=SuccessfulChangeExecutor(),
    )
    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        assert (
            client.post(
                "/api/v1/workspaces/open-local",
                json={
                    "sourcePath": "C:\\projects\\machine.xgwx",
                    "openInXg5000": True,
                },
            ).status_code
            == 200
        )
        conversation = client.post(
            "/api/v1/conversations",
            json={"title": "Missing executable evidence binding"},
        ).json()
        run = store.create_run(
            conversation_id=conversation["conversationId"],
            state=RunState.COMPLETED,
        )
        assert run is not None
        binding = {}
        if binding_case == "conversation_only":
            binding["conversationId"] = conversation["conversationId"]
        elif binding_case == "run_only":
            binding["runId"] = run.id

        rejected = client.post(
            "/api/v1/change-transactions",
            json={
                "operationId": f"missing-binding-{binding_case}-0001",
                **binding,
                "intent": "Replace one operand",
                "riskLevel": "high",
                "plan": ChangePlanV1.model_validate(_plan()).model_dump(
                    mode="json",
                    by_alias=True,
                ),
                "approvalTtlSeconds": 900,
            },
        )

        assert rejected.status_code == 409
        assert (
            "must be bound to one ControlStudio conversation and agent run"
            in rejected.json()["detail"]
        )
        assert client.get("/api/v1/change-transactions").json() == []


def test_knowledge_evidence_cannot_satisfy_executable_change_coverage(
    tmp_path: Path,
) -> None:
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
        event_poll_interval_seconds=0.01,
        launch_token=TEST_LAUNCH_TOKEN_VALUE,
    )
    store = HostStore(config.database_path)
    app = create_app(
        config,
        store=store,
        mcp=VerifiedMCP(),
        change_executor=SuccessfulChangeExecutor(),
    )
    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        assert (
            client.post(
                "/api/v1/workspaces/open-local",
                json={
                    "sourcePath": "C:\\projects\\machine.xgwx",
                    "openInXg5000": True,
                },
            ).status_code
            == 200
        )
        conversation = client.post(
            "/api/v1/conversations",
            json={"title": "Knowledge is not target evidence"},
        ).json()
        run = store.create_run(
            conversation_id=conversation["conversationId"],
            state=RunState.COMPLETED,
        )
        assert run is not None
        knowledge_evidence_id = "knowledge-evidence-unrelated-to-current-project"
        store.append_event(
            event_type="evidence.added",
            conversation_id=conversation["conversationId"],
            run_id=run.id,
            payload={
                "evidenceId": knowledge_evidence_id,
                "title": "Generic motor pattern",
                "kind": "knowledge",
                "toolName": "knowledge.search",
                "summary": "A generic reference that does not prove the current target.",
                "confidence": "supported",
                "knowledgeRef": "knowledge:generic:motor-pattern",
            },
        )

        rejected = client.post(
            "/api/v1/change-transactions",
            json={
                "operationId": "knowledge-evidence-misuse-0001",
                "conversationId": conversation["conversationId"],
                "runId": run.id,
                "intent": "Replace one operand",
                "riskLevel": "high",
                "plan": ChangePlanV1.model_validate(
                    _plan(knowledge_evidence_id)
                ).model_dump(
                    mode="json",
                    by_alias=True,
                ),
                "approvalTtlSeconds": 900,
            },
        )

        assert rejected.status_code == 409
        assert "coverage target_identity has no applicable" in rejected.json()["detail"]
        assert "knowledge.search" in rejected.json()["detail"]
        assert client.get("/api/v1/change-transactions").json() == []


def test_change_api_binds_identity_emits_approval_and_executes_through_service(
    tmp_path: Path,
) -> None:
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
        event_poll_interval_seconds=0.01,
        launch_token=TEST_LAUNCH_TOKEN_VALUE,
    )
    store = HostStore(config.database_path)
    executor = SuccessfulChangeExecutor()
    app = create_app(
        config,
        store=store,
        mcp=VerifiedMCP(),
        change_executor=executor,
    )
    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        opened = client.post(
            "/api/v1/workspaces/open-local",
            json={
                "sourcePath": "C:\\projects\\machine.xgwx",
                "openInXg5000": True,
            },
        )
        assert opened.status_code == 200
        conversation = client.post(
            "/api/v1/conversations",
            json={"title": "Change"},
        ).json()
        run_id, evidence_refs = _seed_change_run_evidence(
            store,
            conversation_id=conversation["conversationId"],
        )
        created = client.post(
            "/api/v1/change-transactions",
            json={
                "operationId": "change-api-operation-0001",
                "conversationId": conversation["conversationId"],
                "runId": run_id,
                "intent": "Replace one operand",
                "riskLevel": "high",
                "plan": ChangePlanV1.model_validate(_plan(evidence_refs)).model_dump(
                    mode="json",
                    by_alias=True,
                ),
                "approvalTtlSeconds": 900,
            },
        )
        assert created.status_code == 201
        payload = created.json()
        transaction = payload["transaction"]
        assert payload["created"] is True
        assert transaction["state"] == "pending_approval"
        assert transaction["targetIdentity"]["workspaceId"] == (
            "11111111-1111-4111-8111-111111111111"
        )
        assert transaction["targetIdentity"]["engineeringSessionId"] == "session-test"
        assert transaction["requestedByActorId"].startswith("browser:")
        assert len(transaction["planDigest"]) == 64

        transaction_id = transaction["transactionId"]
        digest = transaction["planDigest"]
        wrong = client.post(
            f"/api/v1/change-transactions/{transaction_id}/decision",
            json={"decision": "approve", "expectedPlanDigest": "b" * 64},
        )
        assert wrong.status_code == 409
        approved = client.post(
            f"/api/v1/change-transactions/{transaction_id}/decision",
            json={"decision": "approve", "expectedPlanDigest": digest},
        )
        assert approved.status_code == 200
        assert approved.json()["transaction"]["state"] == "approved"
        executed = client.post(
            f"/api/v1/change-transactions/{transaction_id}/execute",
            json={"expectedPlanDigest": digest},
        )
        assert executed.status_code == 200
        assert executed.json()["executionStarted"] is True
        assert executed.json()["transaction"]["state"] == "completed"
        assert executor.states == [
            ChangeTransactionState.EXECUTING,
            ChangeTransactionState.VERIFYING,
        ]

        receipt_url = f"/api/v1/change-transactions/{transaction_id}/receipt-acknowledgement"
        assert client.post(receipt_url).status_code == 403
        csrf_token = client.get("/api/v1/bootstrap").json()["csrfToken"]
        acknowledged = client.post(
            receipt_url,
            headers={"X-ControlStudio-CSRF": csrf_token},
        )
        assert acknowledged.status_code == 200
        assert acknowledged.json()["acknowledgementRecorded"] is True
        assert acknowledged.json()["transaction"]["receiptAcknowledgedAt"] is not None
        assert acknowledged.json()["transaction"]["receiptAcknowledgedBy"].startswith("browser:")
        acknowledged_again = client.post(
            receipt_url,
            headers={"X-ControlStudio-CSRF": csrf_token},
        )
        assert acknowledged_again.status_code == 200
        assert acknowledged_again.json()["acknowledgementRecorded"] is False
        assert (
            acknowledged_again.json()["transaction"]["receiptAcknowledgedAt"]
            == acknowledged.json()["transaction"]["receiptAcknowledgedAt"]
        )

        events = store.list_events(
            conversation_id=conversation["conversationId"],
        )
        approval_requested = [event for event in events if event.event_type == "approval.requested"]
        assert len(approval_requested) == 1
        assert approval_requested[0].payload["transactionId"] == transaction_id
        assert approval_requested[0].payload["transaction"]["planDigest"] == digest
        assert any(event.event_type == "approval.resolved" for event in events)
        update_states = [
            event.payload["transaction"]["state"]
            for event in events
            if event.event_type == "change.transaction.updated"
        ]
        assert update_states == ["executing", "verifying", "completed"]
        assert [
            event.event_type
            for event in events
            if event.event_type == "change.receipt_acknowledged"
        ] == ["change.receipt_acknowledged"]

        # A terminal mutation refresh may invalidate the prior engineering
        # identity.  Re-open and prove the current target before creating the
        # next executable transaction.
        reopened = client.post(
            "/api/v1/workspaces/open-local",
            json={
                "sourcePath": "C:\\projects\\machine.xgwx",
                "openInXg5000": True,
            },
        )
        assert reopened.status_code == 200
        recovery_created = client.post(
            "/api/v1/change-transactions",
            json={
                "operationId": "change-api-operation-recovery",
                "conversationId": conversation["conversationId"],
                "runId": run_id,
                "intent": "Replace one operand",
                "riskLevel": "high",
                "plan": transaction["plan"],
                "approvalTtlSeconds": 900,
            },
        ).json()["transaction"]
        recovery_id = recovery_created["transactionId"]
        recovery_digest = recovery_created["planDigest"]
        assert (
            client.post(
                f"/api/v1/change-transactions/{recovery_id}/decision",
                json={
                    "decision": "approve",
                    "expectedPlanDigest": recovery_digest,
                },
            ).status_code
            == 200
        )
        store.begin_change_execution(
            transaction_id=recovery_id,
            expected_plan_digest=recovery_digest,
        )
        quarantined = store.finish_change_execution(
            transaction_id=recovery_id,
            state=ChangeTransactionState.QUARANTINED,
            error_code="simulated_interruption",
        )
        assert quarantined is not None
        assert store.active_mutation_lease(quarantined.mutation_scope) is not None
        reconciled = client.post(
            f"/api/v1/change-transactions/{recovery_id}/reconcile",
            json={
                "expectedPlanDigest": recovery_digest,
                "pid": 1234,
                "hwnd": 5678,
            },
        )
        assert reconciled.status_code == 200
        assert reconciled.json()["reconciled"] is True
        assert reconciled.json()["transaction"]["state"] == "rolled_back"
        assert store.active_mutation_lease(quarantined.mutation_scope) is None


def test_completed_change_refreshes_active_working_copy_hash(
    tmp_path: Path,
) -> None:
    project_path = (tmp_path / "machine.xgwx").resolve()
    project_path.write_bytes(b"project-before-change")
    original_hash = hashlib.sha256(project_path.read_bytes()).hexdigest()
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
        event_poll_interval_seconds=0.01,
        launch_token=TEST_LAUNCH_TOKEN_VALUE,
    )
    store = HostStore(config.database_path)
    workspace_id = "11111111-1111-4111-8111-111111111111"
    revision_id = "22222222-2222-4222-8222-222222222222"
    store.set_host_state(
        "active_workspace",
        {
            "workspace_id": workspace_id,
            "revision_id": revision_id,
            "source_kind": "local_file",
            "vendor": "ls",
            "engineering_system": "XG5000",
            "created_at": "2026-07-28T12:00:00+00:00",
            "source": {
                "original_path": str(project_path),
                "captured_path": str(project_path),
                "sha256": original_hash,
                "original_state": "unchanged",
                "original_current_sha256": original_hash,
            },
            "working_copy": {
                "path": str(project_path),
                "baseline_sha256": original_hash,
                "current_sha256": original_hash,
                "state": "clean",
            },
        },
    )
    target = ChangeTargetIdentity(
        workspace_id=workspace_id,
        revision_id=revision_id,
        artifact_hash=original_hash,
        source_artifact_hash=original_hash,
        project_path=str(project_path),
        vendor="ls",
        engineering_system="XG5000",
        engineering_session_id="session-test",
        engineering_process_id=1234,
        engineering_window_handle=5678,
    )
    conversation = store.create_conversation(
        title="Refresh saved working-copy identity",
        workspace_id=workspace_id,
        revision_id=revision_id,
        engineering_session_id="session-test",
    )
    run_id, evidence_refs = _seed_change_run_evidence(
        store,
        conversation_id=conversation.id,
    )
    plan = _plan(evidence_refs)
    plan_digest = calculate_plan_digest(
        target=target,
        risk_level=ChangeRiskLevel.HIGH,
        intent="Replace one operand",
        plan=plan,
    )
    transaction, _ = store.create_change_transaction(
        operation_id="change-refresh-working-copy-hash",
        target=target,
        mutation_scope=f"workspace:{workspace_id}",
        risk_level=ChangeRiskLevel.HIGH,
        intent="Replace one operand",
        plan=plan,
        plan_digest=plan_digest,
        conversation_id=conversation.id,
        run_id=run_id,
        requested_by_actor_id="browser:requester",
        approval_expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    app = create_app(
        config,
        store=store,
        mcp=VerifiedMCP(),
        change_executor=FileMutatingSuccessfulChangeExecutor(),
    )

    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        attached = client.post(
            "/api/v1/xg5000/attach",
            json={
                "pid": 1234,
                "hwnd": 5678,
                "projectPath": str(project_path),
                "projectHash": original_hash,
            },
        )
        assert attached.status_code == 200
        approved = client.post(
            f"/api/v1/change-transactions/{transaction.id}/decision",
            json={
                "decision": "approve",
                "expectedPlanDigest": transaction.plan_digest,
            },
        )
        assert approved.status_code == 200
        executed = client.post(
            f"/api/v1/change-transactions/{transaction.id}/execute",
            json={"expectedPlanDigest": transaction.plan_digest},
        )
        assert executed.status_code == 200
        assert executed.json()["transaction"]["state"] == "completed"

    expected_hash = hashlib.sha256(b"saved-project-after-change").hexdigest()
    refreshed = store.get_host_state("active_workspace")
    assert refreshed is not None
    assert refreshed["working_copy"]["current_sha256"] == expected_hash
    assert refreshed["working_copy"]["state"] == "modified"


def test_agent_plan_tool_creates_server_transaction_and_approval_event(
    tmp_path: Path,
) -> None:
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
        event_poll_interval_seconds=0.01,
        launch_token=TEST_LAUNCH_TOKEN_VALUE,
    )
    store = HostStore(config.database_path)
    repository = ModelConnectionRepository(store, MemorySecretStore())
    repository.save(
        base_url="http://localhost:4000/v1",
        model="fake-planning-model",
        timeout_seconds=30,
        api_key=TEST_MODEL_API_KEY_VALUE,
        custom_headers={},
        capability_verified_at=datetime.now(timezone.utc),
    )
    executor = SuccessfulChangeExecutor()
    app = create_app(
        config,
        store=store,
        mcp=VerifiedMCP(),
        model_repository=repository,
        runtime_factory=lambda _settings, _mcp: PlanningRuntime(),
        change_executor=executor,
    )
    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        opened = client.post(
            "/api/v1/workspaces/open-local",
            json={
                "sourcePath": "C:\\projects\\machine.xgwx",
                "openInXg5000": True,
            },
        )
        assert opened.status_code == 200
        conversation = client.post(
            "/api/v1/conversations",
            json={"title": "Natural change"},
        ).json()
        sent = client.post(
            f"/api/v1/conversations/{conversation['conversationId']}/messages",
            json={
                "content": "M0001을 M0002로 잠깐 바꿔 시험해 줘",
                "operationId": "agent-change-request-0001",
                "changeRequestScope": "offline_candidate_revision",
            },
        )
        assert sent.status_code == 202
        run_id = sent.json()["runId"]

        deadline = time.monotonic() + 2
        transactions = []
        while time.monotonic() < deadline:
            transactions = store.list_change_transactions(
                conversation_id=conversation["conversationId"],
            )
            run = store.get_run(run_id)
            if transactions and run is not None and run.state.name == "COMPLETED":
                break
            time.sleep(0.01)

        assert len(transactions) == 1
        completed_run = store.get_run(run_id)
        assert completed_run is not None
        assert completed_run.state is RunState.COMPLETED
        transaction = transactions[0]
        assert transaction.run_id == run_id
        assert transaction.state is ChangeTransactionState.PENDING_APPROVAL
        assert transaction.plan["request_scope"] == "offline_candidate_revision"
        confirmed_refs = {
            evidence_ref
            for item in transaction.plan["coverage"]
            if item["status"] == "confirmed"
            for evidence_ref in item["evidence_refs"]
        }
        run_evidence = {
            event.payload["evidenceId"]
            for event in store.list_run_evidence(
                conversation_id=conversation["conversationId"],
                run_id=run_id,
            )
        }
        assert confirmed_refs
        assert confirmed_refs.issubset(run_evidence)
        request_message = store.get_conversation(
            conversation["conversationId"]
        ).messages[0]
        assert (
            request_message.metadata["change_request_scope"]
            == "offline_candidate_revision"
        )
        events = store.list_events(
            conversation_id=conversation["conversationId"],
        )
        approval = [event for event in events if event.event_type == "approval.requested"]
        assert len(approval) == 1
        assert approval[0].payload["transactionId"] == transaction.id

        approved = client.post(
            f"/api/v1/change-transactions/{transaction.id}/decision",
            json={
                "decision": "approve",
                "expectedPlanDigest": transaction.plan_digest,
            },
        )
        assert approved.status_code == 200
        assert approved.json()["transaction"]["state"] == "approved"

        # Evidence is part of the execution precondition, not a creation-time
        # decoration. If the exact-run evidence disappears after approval, the
        # Host must fail closed before invoking the mutation executor.
        with sqlite3.connect(store.database_path) as connection:
            connection.execute(
                """
                DELETE FROM events
                WHERE conversation_id = ?
                  AND run_id = ?
                  AND event_type = 'evidence.added'
                """,
                (conversation["conversationId"], run_id),
            )
            connection.commit()
        execution = client.post(
            f"/api/v1/change-transactions/{transaction.id}/execute",
            json={"expectedPlanDigest": transaction.plan_digest},
        )
        assert execution.status_code == 409
        assert "evidenceRefs do not resolve" in execution.json()["detail"]
        assert executor.states == []


@pytest.mark.parametrize(
    ("content", "operation_id"),
    [
        (
            "이 타이머를 잠깐 늘리면 어떤 영향인지 분석해 줘",
            "default-analysis-temporary-0001",
        ),
        (
            "M0001을 M0002로 바꿔 줘",
            "default-analysis-ordinary-0001",
        ),
    ],
)
def test_change_request_defaults_to_analysis_only_until_offline_scope_selected(
    tmp_path: Path,
    content: str,
    operation_id: str,
) -> None:
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
        event_poll_interval_seconds=0.01,
        launch_token=TEST_LAUNCH_TOKEN_VALUE,
    )
    store = HostStore(config.database_path)
    repository = ModelConnectionRepository(store, MemorySecretStore())
    repository.save(
        base_url="http://localhost:4000/v1",
        model="fake-planning-model",
        timeout_seconds=30,
        api_key=TEST_MODEL_API_KEY_VALUE,
        custom_headers={},
        capability_verified_at=datetime.now(timezone.utc),
    )
    runtime = PlannerBindingObservationRuntime()
    app = create_app(
        config,
        store=store,
        mcp=VerifiedMCP(),
        model_repository=repository,
        runtime_factory=lambda _settings, _mcp: runtime,
    )
    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        assert (
            client.post(
                "/api/v1/workspaces/open-local",
                json={
                    "sourcePath": "C:\\projects\\machine.xgwx",
                    "openInXg5000": True,
                },
            ).status_code
            == 200
        )
        conversation = client.post(
            "/api/v1/conversations",
            json={"title": "Temporary analysis only"},
        ).json()
        sent = client.post(
            f"/api/v1/conversations/{conversation['conversationId']}/messages",
            json={
                "content": content,
                "operationId": operation_id,
            },
        )
        assert sent.status_code == 202
        run_id = sent.json()["runId"]
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            run = store.get_run(run_id)
            if run is not None and run.state in {
                RunState.COMPLETED,
                RunState.FAILED,
            }:
                break
            time.sleep(0.01)

    assert runtime.planner_bound is False
    assert runtime.trusted_context["change_request_scope"] == "analysis_only"
    detail = store.get_conversation(conversation["conversationId"])
    assert detail is not None
    assert detail.messages[0].metadata["change_request_scope"] == "analysis_only"
    assert store.list_change_transactions(
        conversation_id=conversation["conversationId"],
    ) == []


def test_agent_change_plan_rejects_fabricated_evidence_reference(
    tmp_path: Path,
) -> None:
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
        event_poll_interval_seconds=0.01,
        launch_token=TEST_LAUNCH_TOKEN_VALUE,
    )
    store = HostStore(config.database_path)
    repository = ModelConnectionRepository(store, MemorySecretStore())
    repository.save(
        base_url="http://localhost:4000/v1",
        model="fake-planning-model",
        timeout_seconds=30,
        api_key=TEST_MODEL_API_KEY_VALUE,
        custom_headers={},
        capability_verified_at=datetime.now(timezone.utc),
    )
    app = create_app(
        config,
        store=store,
        mcp=VerifiedMCP(),
        model_repository=repository,
        runtime_factory=lambda _settings, _mcp: PlanningRuntime(
            evidence_override="fabricated-evidence-reference",
        ),
    )
    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        assert (
            client.post(
                "/api/v1/workspaces/open-local",
                json={
                    "sourcePath": "C:\\projects\\machine.xgwx",
                    "openInXg5000": True,
                },
            ).status_code
            == 200
        )
        conversation = client.post(
            "/api/v1/conversations",
            json={"title": "Fabricated evidence"},
        ).json()
        sent = client.post(
            f"/api/v1/conversations/{conversation['conversationId']}/messages",
            json={
                "content": "M0001을 M0002로 바꿔 줘",
                "operationId": "fabricated-evidence-run-0001",
                "changeRequestScope": "offline_candidate_revision",
            },
        )
        assert sent.status_code == 202
        run_id = sent.json()["runId"]
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            run = store.get_run(run_id)
            if run is not None and run.state in {
                RunState.COMPLETED,
                RunState.FAILED,
            }:
                break
            time.sleep(0.01)

    run = store.get_run(run_id)
    assert run is not None
    assert run.state is RunState.FAILED
    assert "evidenceRefs do not resolve" in (run.error_detail or "")
    assert store.list_change_transactions(
        conversation_id=conversation["conversationId"],
    ) == []


def test_controlled_read_session_does_not_offer_change_approval_contract(
    tmp_path: Path,
) -> None:
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
        event_poll_interval_seconds=0.01,
        launch_token=TEST_LAUNCH_TOKEN_VALUE,
    )
    store = HostStore(config.database_path)
    repository = ModelConnectionRepository(store, MemorySecretStore())
    repository.save(
        base_url="http://localhost:4000/v1",
        model="fake-planning-model",
        timeout_seconds=30,
        api_key=TEST_MODEL_API_KEY_VALUE,
        custom_headers={},
        capability_verified_at=datetime.now(timezone.utc),
    )
    runtime = PlannerBindingObservationRuntime()
    app = create_app(
        config,
        store=store,
        mcp=FakeMCP(),
        model_repository=repository,
        runtime_factory=lambda _settings, _mcp: runtime,
    )
    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        opened = client.post(
            "/api/v1/workspaces/open-local",
            json={
                "sourcePath": "C:\\projects\\machine.xgwx",
                "openInXg5000": True,
            },
        )
        assert opened.status_code == 200
        assert (
            opened.json()["session"]["metadata"]["project_identity_verified_against_selected_pid"]
            is False
        )
        conversation = client.post(
            "/api/v1/conversations",
            json={"title": "Read-only recommendation"},
        ).json()
        sent = client.post(
            f"/api/v1/conversations/{conversation['conversationId']}/messages",
            json={
                "content": "변경 대안을 설명해 줘",
                "operationId": "read-only-recommendation-0001",
            },
        )
        assert sent.status_code == 202
        run_id = sent.json()["runId"]
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            run = store.get_run(run_id)
            if run is not None and run.state in {
                RunState.COMPLETED,
                RunState.FAILED,
            }:
                break
            time.sleep(0.01)

    run = store.get_run(run_id)
    assert run is not None
    assert run.state is RunState.COMPLETED
    assert runtime.planner_bound is False
    assert (
        store.list_change_transactions(
            conversation_id=conversation["conversationId"],
        )
        == []
    )
    detail = store.get_conversation(conversation["conversationId"])
    assert detail is not None
    assert detail.messages[-1].content == "planner_bound=False"
