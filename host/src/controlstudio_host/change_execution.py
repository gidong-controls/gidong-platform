from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from .mcp_client import MCPGateway, MCPToolError
from .models import (
    ChangeRiskLevel,
    ChangeTargetIdentity,
    ChangeTransaction,
    ChangeTransactionState,
)

VERIFICATION_SCOPE = "offline_native_check_only"
OFFLINE_NATIVE_VALIDATION_STEPS = (
    "structural_diff",
    "xg5000_program_check",
    "project_save",
)
NOT_EXECUTED_VALIDATION_TIERS = (
    "project_reopen",
    "xg_sim",
    "runtime_or_online_validation",
)
NativeCheckpointCallback = Callable[[str, dict[str, Any]], None]


def calculate_plan_digest(
    *,
    target: ChangeTargetIdentity,
    risk_level: ChangeRiskLevel,
    intent: str,
    plan: dict[str, Any],
) -> str:
    """Bind approval to the exact target identity and canonical change plan."""

    contract = {
        "schema_version": "1",
        "target": target.model_dump(mode="json"),
        "risk_level": risk_level.value,
        "intent": intent,
        "plan": plan,
    }
    canonical = json.dumps(
        contract,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class ChangeApplyResult:
    """Evidence returned after an adapter has attempted the native mutation."""

    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChangeRecoveryTarget:
    """A user-selected XG5000 window used only for explicit recovery."""

    pid: int
    hwnd: int

    def __post_init__(self) -> None:
        if self.pid <= 0 or self.hwnd <= 0:
            raise ValueError("recovery PID and HWND must be positive")


@dataclass(frozen=True)
class ChangeVerificationResult:
    """Final, evidence-backed outcome returned by a change execution adapter."""

    state: ChangeTransactionState
    evidence: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_detail: str | None = None

    def __post_init__(self) -> None:
        allowed = {
            ChangeTransactionState.OBSERVING,
            ChangeTransactionState.COMPLETED,
            ChangeTransactionState.ROLLED_BACK,
            ChangeTransactionState.FAILED,
            ChangeTransactionState.QUARANTINED,
        }
        if self.state not in allowed:
            raise ValueError("verification result must be a terminal execution state")


class ChangeExecutionService(Protocol):
    """Seam for a future MCP-backed apply/readback/rollback implementation."""

    async def preflight(self, transaction: ChangeTransaction) -> None: ...

    async def apply(self, transaction: ChangeTransaction) -> ChangeApplyResult: ...

    async def verify(
        self,
        transaction: ChangeTransaction,
        apply_result: ChangeApplyResult,
    ) -> ChangeVerificationResult: ...

    async def reconcile(
        self,
        transaction: ChangeTransaction,
        recovery_target: ChangeRecoveryTarget,
    ) -> ChangeVerificationResult: ...


class ChangeExecutionPreconditionError(RuntimeError):
    """The immutable plan cannot be mapped to a supported native mutation."""


class ChangeExecutionFailedSafely(RuntimeError):
    """Execution failed before the native mutation could have been applied."""


class ChangeExecutionOutcomeUncertain(RuntimeError):
    """A native mutation may have happened and must be reconciled."""

    def __init__(
        self,
        message: str,
        *,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.evidence = evidence or {}


class MCPChangeExecutionService:
    """Production Host-owned offline mutation path through MCPGateway."""

    def __init__(
        self,
        gateway: MCPGateway,
        *,
        checkpoint_callback: NativeCheckpointCallback | None = None,
    ) -> None:
        self._gateway = gateway
        self._checkpoint_callback = checkpoint_callback

    async def preflight(self, transaction: ChangeTransaction) -> None:
        target = transaction.target
        if (
            not target.engineering_session_id
            or target.engineering_process_id is None
            or target.engineering_window_handle is None
        ):
            raise ChangeExecutionPreconditionError(
                "a verified engineering session is required before offline mutation"
            )
        native_plan = self._native_plan_arguments(transaction)
        if not {
            "analysis_digest",
            "hardware_context_digest",
            "network_id",
            "network_digest",
        }.issubset(native_plan):
            raise ChangeExecutionPreconditionError(
                "offline execution requires a native change-target analysis receipt"
            )
        self._validate_verification_contract(transaction)

    async def apply(self, transaction: ChangeTransaction) -> ChangeApplyResult:
        await self.preflight(transaction)
        target = transaction.target
        session_scope = self._session_scope(transaction)
        try:
            snapshot = self._require_object(
                await self._gateway.call_change_tool(
                    "project.snapshot",
                    {
                        **session_scope,
                        "workspace_id": target.workspace_id,
                        "begin_change_transaction": True,
                        "host_transaction_id": transaction.id,
                    },
                ),
                "project.snapshot",
            )
            mcp_transaction_id = self._required_text(
                snapshot,
                "transaction_id",
                "project.snapshot",
            )
            project_identity_digest = self._project_identity_digest(target)
            await self._persist_checkpoint_or_abort(
                transaction,
                {
                    "stage": "SNAPSHOT_CREATED",
                    "mcp_transaction_id": mcp_transaction_id,
                    "snapshot_id": self._required_text(
                        snapshot,
                        "snapshot_id",
                        "project.snapshot",
                    ),
                    "session_id": target.engineering_session_id,
                    "workspace_id": target.workspace_id,
                    "project_path": target.project_path,
                    "project_hash": target.artifact_hash,
                    "project_identity_digest": project_identity_digest,
                },
                session_scope=session_scope,
                mcp_transaction_id=mcp_transaction_id,
                expected_mcp_state="SNAPSHOT_CREATED",
            )
            backup = self._require_object(
                await self._gateway.call_change_tool(
                    "project.backup",
                    {
                        **session_scope,
                        "transaction_id": mcp_transaction_id,
                    },
                ),
                "project.backup",
            )
            backup_id = self._required_text(
                backup,
                "backup_id",
                "project.backup",
            )
            await self._persist_checkpoint_or_abort(
                transaction,
                {
                    "stage": "BACKUP_CREATED",
                    "mcp_transaction_id": mcp_transaction_id,
                    "snapshot_id": self._required_text(
                        snapshot,
                        "snapshot_id",
                        "project.snapshot",
                    ),
                    "backup_id": backup_id,
                    "session_id": target.engineering_session_id,
                    "workspace_id": target.workspace_id,
                    "project_path": target.project_path,
                    "project_hash": target.artifact_hash,
                    "project_identity_digest": project_identity_digest,
                },
                session_scope=session_scope,
                mcp_transaction_id=mcp_transaction_id,
                expected_mcp_state="BACKUP_CREATED",
            )
            plan_arguments = {
                **session_scope,
                "transaction_id": mcp_transaction_id,
                "snapshot_id": self._required_text(
                    snapshot,
                    "snapshot_id",
                    "project.snapshot",
                ),
                "backup_id": backup_id,
                **self._native_plan_arguments(transaction),
            }
            planned = self._require_object(
                await self._gateway.call_change_tool(
                    "program.plan_change",
                    plan_arguments,
                ),
                "program.plan_change",
            )
            plan_contract = (
                planned["plan"]
                if isinstance(planned.get("plan"), dict)
                else planned
            )
            mcp_plan_id = self._required_text(
                plan_contract,
                "plan_id",
                "program.plan_change",
            )
            mcp_plan_digest = self._required_text(
                plan_contract,
                "plan_digest",
                "program.plan_change",
            )
            returned_project_identity_digest = self._required_text(
                plan_contract,
                "project_identity_digest",
                "program.plan_change",
            )
            self._validate_mcp_plan_contract(
                transaction=transaction,
                plan_contract=plan_contract,
                plan_arguments=plan_arguments,
                snapshot=snapshot,
            )
            if returned_project_identity_digest != project_identity_digest:
                raise ChangeExecutionPreconditionError(
                    "program.plan_change returned a mismatched "
                    "project_identity_digest"
                )
            await self._persist_checkpoint_or_abort(
                transaction,
                {
                    "stage": "PLANNED",
                    "mcp_transaction_id": mcp_transaction_id,
                    "mcp_plan_id": mcp_plan_id,
                    "mcp_plan_digest": mcp_plan_digest,
                    "snapshot_id": plan_arguments["snapshot_id"],
                    "backup_id": backup_id,
                    "session_id": target.engineering_session_id,
                    "workspace_id": target.workspace_id,
                    "project_path": target.project_path,
                    "project_hash": target.artifact_hash,
                    "project_identity_digest": project_identity_digest,
                    **self._native_plan_arguments(transaction),
                },
                session_scope=session_scope,
                mcp_transaction_id=mcp_transaction_id,
                expected_mcp_state="PLANNED",
            )
        except (
            MCPToolError,
            ChangeExecutionPreconditionError,
            ValueError,
            TypeError,
        ) as exc:
            raise ChangeExecutionFailedSafely(str(exc)) from exc

        apply_arguments = {
            **session_scope,
            "transaction_id": mcp_transaction_id,
            "plan_id": mcp_plan_id,
        }
        authorization = self._authorization(
            transaction=transaction,
            mcp_transaction_id=mcp_transaction_id,
            mcp_plan_id=mcp_plan_id,
            mcp_plan_digest=mcp_plan_digest,
            project_identity_digest=project_identity_digest,
            mutation_arguments=apply_arguments,
        )
        try:
            applied = self._require_object(
                await self._gateway.call_change_tool(
                    "program.apply_ld_patch",
                    {
                        **apply_arguments,
                        "authorization": authorization,
                    },
                ),
                "program.apply_ld_patch",
            )
        except (MCPToolError, ValueError, TypeError) as exc:
            raise ChangeExecutionOutcomeUncertain(
                "MCP apply result is uncertain and requires transaction reconciliation",
                evidence={
                    "mcp_transaction_id": mcp_transaction_id,
                    "mcp_plan_id": mcp_plan_id,
                    "mcp_plan_digest": mcp_plan_digest,
                    "project_identity_digest": project_identity_digest,
                    "snapshot": snapshot,
                    "backup": backup,
                },
            ) from exc

        return ChangeApplyResult(
            evidence={
                "host_transaction_id": transaction.id,
                "host_plan_digest": transaction.plan_digest,
                "mcp_transaction_id": mcp_transaction_id,
                "mcp_plan_id": mcp_plan_id,
                "mcp_plan_digest": mcp_plan_digest,
                "project_identity_digest": project_identity_digest,
                "snapshot": snapshot,
                "backup": backup,
                "apply": applied,
            }
        )

    async def verify(
        self,
        transaction: ChangeTransaction,
        apply_result: ChangeApplyResult,
    ) -> ChangeVerificationResult:
        evidence = apply_result.evidence
        mcp_transaction_id = self._required_text(
            evidence,
            "mcp_transaction_id",
            "apply evidence",
        )
        mcp_plan_id = self._required_text(
            evidence,
            "mcp_plan_id",
            "apply evidence",
        )
        session_scope = self._session_scope(transaction)
        verification = self._verification_contract_evidence(transaction)
        try:
            diff_result = self._require_object(
                await self._gateway.call_change_tool(
                    "project.diff",
                    {
                        **session_scope,
                        "transaction_id": mcp_transaction_id,
                        "plan_id": mcp_plan_id,
                    },
                ),
                "project.diff",
            )
            self._validate_diff_result(
                transaction=transaction,
                result=diff_result,
                mcp_transaction_id=mcp_transaction_id,
                mcp_plan_id=mcp_plan_id,
            )
            verification["diff"] = diff_result
            validation_result = self._require_object(
                await self._gateway.call_change_tool(
                    "project.validate_change",
                    {
                        **session_scope,
                        "transaction_id": mcp_transaction_id,
                        "plan_id": mcp_plan_id,
                        "rollback_after_validation": False,
                    },
                ),
                "project.validate_change",
            )
            self._validate_validation_result(
                transaction=transaction,
                result=validation_result,
                mcp_transaction_id=mcp_transaction_id,
                mcp_plan_id=mcp_plan_id,
            )
            verification["validation"] = validation_result
            save_result = self._require_object(
                await self._gateway.call_change_tool(
                    "project.commit_save",
                    {
                        **session_scope,
                        "transaction_id": mcp_transaction_id,
                    },
                ),
                "project.commit_save",
            )
            self._validate_save_result(
                transaction=transaction,
                result=save_result,
                mcp_transaction_id=mcp_transaction_id,
                expected_state="SAVED",
            )
            verification["save"] = save_result
            session_scope, saved_status = await self._authoritative_scope_after_save(
                transaction=transaction,
                mcp_transaction_id=mcp_transaction_id,
                fallback=session_scope,
                save_result=save_result,
            )
            verification["saved_transaction_status"] = saved_status
            commit_result = self._require_object(
                await self._gateway.call_change_tool(
                    "change.commit",
                    {
                        **session_scope,
                        "transaction_id": mcp_transaction_id,
                    },
                ),
                "change.commit",
            )
            self._validate_commit_result(
                transaction=transaction,
                result=commit_result,
                mcp_transaction_id=mcp_transaction_id,
                expected_project_hash=session_scope["project_hash"],
            )
            verification["commit"] = commit_result
        except (
            MCPToolError,
            ChangeExecutionPreconditionError,
            ValueError,
            TypeError,
        ) as verification_error:
            try:
                recovery_scope, recovery_status = (
                    await self._authoritative_scope_after_save(
                        transaction=transaction,
                        mcp_transaction_id=mcp_transaction_id,
                        fallback=session_scope,
                        save_result=None,
                    )
                )
                verification["recovery_transaction_status"] = recovery_status
                rollback = self._require_object(
                    await self._gateway.call_change_tool(
                        "project.rollback_change",
                        {
                            **recovery_scope,
                            "transaction_id": mcp_transaction_id,
                            "plan_id": mcp_plan_id,
                        },
                    ),
                    "project.rollback_change",
                )
                self._validate_rollback_result(
                    transaction=transaction,
                    result=rollback,
                    mcp_transaction_id=mcp_transaction_id,
                    mcp_plan_id=mcp_plan_id,
                )
                rollback_save = self._require_object(
                    await self._gateway.call_change_tool(
                        "project.commit_save",
                        {
                            **recovery_scope,
                            "transaction_id": mcp_transaction_id,
                        },
                    ),
                    "project.commit_save",
                )
                self._validate_save_result(
                    transaction=transaction,
                    result=rollback_save,
                    mcp_transaction_id=mcp_transaction_id,
                    expected_state="ROLLED_BACK_SAVED",
                )
            except (
                MCPToolError,
                ChangeExecutionPreconditionError,
                ValueError,
                TypeError,
            ) as rollback_error:
                return ChangeVerificationResult(
                    state=ChangeTransactionState.QUARANTINED,
                    evidence={
                        **verification,
                        "verification_error": str(verification_error),
                        "rollback_error": str(rollback_error),
                    },
                    error_code="rollback_outcome_uncertain",
                    error_detail=(
                        "Verification failed and rollback could not be proven."
                    ),
                )
            return ChangeVerificationResult(
                state=ChangeTransactionState.ROLLED_BACK,
                evidence={
                    **verification,
                    "verification_error": str(verification_error),
                    "rollback": rollback,
                    "rollback_save": rollback_save,
                },
                error_code="verification_failed_rolled_back",
                error_detail="Verification failed; the pinned backup was restored and saved.",
            )

        return ChangeVerificationResult(
            state=ChangeTransactionState.COMPLETED,
            evidence={
                **verification,
                "completion_claim": (
                    "offline_project_saved_with_limited_native_verification"
                ),
            },
        )

    async def reconcile(
        self,
        transaction: ChangeTransaction,
        recovery_target: ChangeRecoveryTarget,
    ) -> ChangeVerificationResult:
        self._native_plan_arguments(transaction)
        self._validate_verification_contract(transaction)
        target = transaction.target
        try:
            lookup = self._require_object(
                await self._gateway.call_recovery_tool(
                    "change.transaction_lookup",
                    {"host_transaction_id": transaction.id},
                ),
                "change.transaction_lookup",
            )
            checkpoint = self._recovery_checkpoint(
                lookup,
                "transaction_checkpoint",
                "change.transaction_lookup",
            )
            stored_checkpoint = self._stored_native_checkpoint(transaction)
            if stored_checkpoint is not None:
                stored_to_native = {
                    "transaction_id": "transaction_id",
                    "snapshot_id": "snapshot_id",
                    "backup_id": "backup_id",
                    "plan_id": "plan_id",
                    "plan_digest": "plan_digest",
                    "workspace_id": "workspace_id",
                    "project_identity_digest": "project_identity_digest",
                    "program_id": "program_id",
                    "token_offset": "token_offset",
                    "expected": "expected",
                    "replacement": "replacement",
                }
                for stored_field, native_field in stored_to_native.items():
                    stored_value = stored_checkpoint.get(stored_field)
                    if (
                        stored_value is not None
                        and stored_value != checkpoint.get(native_field)
                    ):
                        raise ValueError(
                            "Host and MCP checkpoints disagree on immutable "
                            f"field {native_field}"
                        )
                if checkpoint.get("transaction_id") is None:
                    raise ValueError(
                        "MCP recovery checkpoint has no native transaction id"
                    )
            self._validate_recovery_checkpoint(
                transaction=transaction,
                checkpoint=checkpoint,
            )
        except (MCPToolError, ValueError, TypeError) as exc:
            return ChangeVerificationResult(
                state=ChangeTransactionState.QUARANTINED,
                evidence={"recovery_bootstrap_error": str(exc)},
                error_code="recovery_identity_unavailable",
                error_detail="The native MCP transaction cannot be identified safely.",
            )

        mcp_transaction_id = self._required_text(
            checkpoint,
            "transaction_id",
            "native recovery checkpoint",
        )
        native_state = str(checkpoint.get("state") or "")
        recovery_action = str(checkpoint.get("recovery_action") or "")
        lookup_evidence = {
            **self._verification_contract_evidence(transaction),
            "lookup": lookup,
        }
        if recovery_action == "already_terminal" and native_state == "COMMITTED":
            return ChangeVerificationResult(
                state=ChangeTransactionState.COMPLETED,
                evidence=lookup_evidence,
            )
        if recovery_action == "already_terminal" and native_state in {
            "ROLLED_BACK_SAVED",
            "ABORTED_NO_MUTATION",
        }:
            return ChangeVerificationResult(
                state=ChangeTransactionState.ROLLED_BACK,
                evidence={
                    **lookup_evidence,
                    "native_mutation_performed": native_state
                    != "ABORTED_NO_MUTATION",
                },
            )
        try:
            current_project_hash = self._hash_project_file(target.project_path)
        except OSError as exc:
            return ChangeVerificationResult(
                state=ChangeTransactionState.QUARANTINED,
                evidence={**lookup_evidence, "project_hash_error": str(exc)},
                error_code="recovery_project_unavailable",
                error_detail="The recovery working-copy file cannot be verified.",
            )
        expected_native_plan_digest = checkpoint.get("plan_digest")
        attach_arguments: dict[str, Any] = {
            "pid": recovery_target.pid,
            "hwnd": recovery_target.hwnd,
            "project_path": target.project_path,
            "project_hash": current_project_hash,
            "workspace_id": target.workspace_id,
            "recover_transaction_id": mcp_transaction_id,
            "access_profile": "analysis",
        }
        if isinstance(expected_native_plan_digest, str) and expected_native_plan_digest:
            attach_arguments["recover_expected_plan_digest"] = (
                expected_native_plan_digest
            )
        try:
            attached = self._require_object(
                await self._gateway.call_recovery_tool(
                    "xg5000.attach",
                    attach_arguments,
                ),
                "xg5000.attach recovery",
            )
            recovery_session_id = self._validate_recovery_attach(
                transaction=transaction,
                recovery_target=recovery_target,
                expected_project_hash=current_project_hash,
                expected_transaction_id=mcp_transaction_id,
                attached=attached,
            )
            status_result = self._require_object(
                await self._gateway.call_change_tool(
                    "change.transaction_status",
                    {
                        "session_id": recovery_session_id,
                        "transaction_id": mcp_transaction_id,
                    },
                ),
                "change.transaction_status",
            )
            checkpoint = self._recovery_checkpoint(
                status_result,
                "transaction_checkpoint",
                "change.transaction_status",
            )
            self._validate_recovery_checkpoint(
                transaction=transaction,
                checkpoint=checkpoint,
                expected_transaction_id=mcp_transaction_id,
                expected_session_id=recovery_session_id,
                expected_pid=recovery_target.pid,
                expected_hwnd=recovery_target.hwnd,
                expected_project_hash=current_project_hash,
            )
        except (MCPToolError, ValueError, TypeError) as exc:
            return ChangeVerificationResult(
                state=ChangeTransactionState.QUARANTINED,
                evidence={"recovery_attach_or_status_error": str(exc)},
                error_code="native_status_unavailable",
                error_detail=(
                    "Recovery-only XG5000 attachment or native status proof failed."
                ),
            )

        native_state = str(checkpoint.get("state") or "")
        recovery_action = str(checkpoint.get("recovery_action") or "")
        evidence: dict[str, Any] = {
            **self._verification_contract_evidence(transaction),
            "lookup": lookup,
            "recovery_attach": attached,
            "status": status_result,
        }

        recovery_scope = {
            "session_id": recovery_session_id,
            "pid": recovery_target.pid,
            "hwnd": recovery_target.hwnd,
            "project_path": target.project_path,
            "project_hash": self._required_sha256(
                checkpoint,
                "current_project_hash",
                "change.transaction_status",
            ),
        }
        if recovery_action == "abort_safe":
            try:
                aborted = self._require_object(
                    await self._gateway.call_change_tool(
                        "change.abort",
                        {
                            **recovery_scope,
                            "transaction_id": mcp_transaction_id,
                            "expected_transaction_state": native_state,
                        },
                    ),
                    "change.abort",
                )
                aborted_checkpoint = self._recovery_checkpoint(
                    aborted,
                    "transaction_checkpoint",
                    "change.abort",
                )
                self._validate_recovery_checkpoint(
                    transaction=transaction,
                    checkpoint=aborted_checkpoint,
                    expected_transaction_id=mcp_transaction_id,
                    expected_session_id=recovery_session_id,
                    expected_pid=recovery_target.pid,
                    expected_hwnd=recovery_target.hwnd,
                )
                if (
                    aborted.get("state_after") != "ABORTED_NO_MUTATION"
                    or aborted.get("native_mutation_performed") is not False
                    or aborted.get("lease_released") is not True
                    or aborted_checkpoint.get("state") != "ABORTED_NO_MUTATION"
                    or aborted_checkpoint.get("terminal") is not True
                ):
                    raise ValueError(
                        "change.abort did not prove a terminal no-mutation abort"
                    )
            except (MCPToolError, ValueError, TypeError) as exc:
                evidence["abort_error"] = str(exc)
                return ChangeVerificationResult(
                    state=ChangeTransactionState.QUARANTINED,
                    evidence=evidence,
                    error_code="native_abort_unproven",
                    error_detail="The pre-mutation native transaction could not be aborted.",
                )
            return ChangeVerificationResult(
                state=ChangeTransactionState.ROLLED_BACK,
                evidence={
                    **evidence,
                    "abort": aborted,
                    "native_mutation_performed": False,
                },
                error_code="explicit_recovery_aborted_before_mutation",
                error_detail=(
                    "The interrupted native transaction was aborted before mutation."
                ),
            )

        if recovery_action != "rollback_required":
            return ChangeVerificationResult(
                state=ChangeTransactionState.QUARANTINED,
                evidence=evidence,
                error_code="manual_native_recovery_required",
                error_detail=(
                    "The native checkpoint does not permit automatic abort or rollback."
                ),
            )
        mcp_plan_id = self._required_text(
            checkpoint,
            "plan_id",
            "change.transaction_status",
        )
        try:
            evidence["rollback"] = self._require_object(
                await self._gateway.call_change_tool(
                    "project.rollback_change",
                    {
                        **recovery_scope,
                        "transaction_id": mcp_transaction_id,
                        "plan_id": mcp_plan_id,
                    },
                ),
                "project.rollback_change",
            )
            evidence["rollback_save"] = self._require_object(
                await self._gateway.call_change_tool(
                    "project.commit_save",
                    {
                        **recovery_scope,
                        "transaction_id": mcp_transaction_id,
                    },
                ),
                "project.commit_save",
            )
            final_status = self._require_object(
                await self._gateway.call_change_tool(
                    "change.transaction_status",
                    {
                        "session_id": recovery_session_id,
                        "transaction_id": mcp_transaction_id,
                    },
                ),
                "change.transaction_status after rollback",
            )
            final_checkpoint = self._recovery_checkpoint(
                final_status,
                "transaction_checkpoint",
                "change.transaction_status after rollback",
            )
            self._validate_recovery_checkpoint(
                transaction=transaction,
                checkpoint=final_checkpoint,
                expected_transaction_id=mcp_transaction_id,
                expected_session_id=recovery_session_id,
                expected_pid=recovery_target.pid,
                expected_hwnd=recovery_target.hwnd,
            )
            if (
                final_checkpoint.get("state") != "ROLLED_BACK_SAVED"
                or final_checkpoint.get("terminal") is not True
                or final_checkpoint.get("recovery_action") != "already_terminal"
            ):
                raise ValueError(
                    "rollback did not reach the terminal ROLLED_BACK_SAVED state"
                )
            evidence["final_status"] = final_status
        except (MCPToolError, ValueError, TypeError) as exc:
            evidence["rollback_error"] = str(exc)
            return ChangeVerificationResult(
                state=ChangeTransactionState.QUARANTINED,
                evidence=evidence,
                error_code="explicit_recovery_unproven",
                error_detail="Explicit rollback could not be proven.",
            )
        return ChangeVerificationResult(
            state=ChangeTransactionState.ROLLED_BACK,
            evidence=evidence,
            error_code="explicit_recovery_rolled_back",
            error_detail="The quarantined native transaction was explicitly rolled back.",
        )

    def _authorization(
        self,
        *,
        transaction: ChangeTransaction,
        mcp_transaction_id: str,
        mcp_plan_id: str,
        mcp_plan_digest: str,
        project_identity_digest: str,
        mutation_arguments: dict[str, Any],
    ) -> dict[str, Any]:
        issued_at = datetime.now(timezone.utc)
        approval = transaction.approval
        if approval is None:
            raise ChangeExecutionPreconditionError(
                "a durable one-time approval is required"
            )
        target = transaction.target
        target_identity_digest = self._mcp_target_identity_digest(
            session_id=target.engineering_session_id,
            pid=target.engineering_process_id,
            hwnd=target.engineering_window_handle,
            project_path=target.project_path,
            project_hash=target.artifact_hash,
            workspace_id=target.workspace_id,
            project_identity_digest=project_identity_digest,
        )
        arguments_digest = self._mcp_mutation_arguments_digest(
            operation="program.apply_ld_patch",
            arguments={
                **mutation_arguments,
                "plan_digest": mcp_plan_digest,
                "workspace_id": target.workspace_id,
                "project_identity_digest": project_identity_digest,
            },
        )
        return self._gateway.sign_mutation_authorization(
            {
                "schema": "controlstudio-change-authorization/v1",
                "authorization_id": str(uuid.uuid4()),
                "actor_id": approval.actor_id,
                "transaction_id": mcp_transaction_id,
                "session_id": transaction.target.engineering_session_id,
                "workspace_id": transaction.target.workspace_id,
                "project_identity_digest": project_identity_digest,
                "target_identity_digest": target_identity_digest,
                "plan_id": mcp_plan_id,
                "plan_digest": mcp_plan_digest,
                "capability": "offline_mutate",
                "allowed_operation": "program.apply_ld_patch",
                "arguments_digest": arguments_digest,
                "issued_at": issued_at.isoformat(timespec="milliseconds"),
                "expires_at": (
                    issued_at + timedelta(minutes=2)
                ).isoformat(timespec="milliseconds"),
                "nonce": str(uuid.uuid4()),
            }
        )

    @staticmethod
    def _native_plan_arguments(transaction: ChangeTransaction) -> dict[str, Any]:
        plan = transaction.plan
        target = plan.get("target")
        current = plan.get("current")
        proposed = plan.get("proposed")
        if not all(isinstance(value, dict) for value in (target, current, proposed)):
            raise ChangeExecutionPreconditionError(
                "change plan target/current/proposed must be objects"
            )
        assert isinstance(target, dict)
        assert isinstance(current, dict)
        assert isinstance(proposed, dict)
        program = target.get(
            "program_id",
            target.get("programId", target.get("program")),
        )
        token = target.get(
            "token_offset",
            target.get("tokenOffset", target.get("token")),
        )
        expected = current.get("value", current.get("operand"))
        replacement = proposed.get("value", proposed.get("operand"))
        analysis_digest = target.get(
            "analysis_digest",
            target.get("analysisDigest"),
        )
        network_id = target.get("network_id", target.get("networkId"))
        network_digest = target.get(
            "network_digest",
            target.get("networkDigest"),
        )
        hardware_context_digest = target.get(
            "hardware_context_digest",
            target.get("hardwareContextDigest"),
        )
        values: dict[str, Any] = {
            "program_id": program,
            "token_offset": token,
            "expected": expected,
            "replacement": replacement,
        }
        analysis_values = {
            "analysis_digest": analysis_digest,
            "network_id": network_id,
            "network_digest": network_digest,
        }
        if any(value not in {None, ""} for value in analysis_values.values()):
            values.update(analysis_values)
        if hardware_context_digest not in {None, ""}:
            values["hardware_context_digest"] = hardware_context_digest
        missing = [
            name for name, value in values.items() if value is None or value == ""
        ]
        if missing:
            raise ChangeExecutionPreconditionError(
                "change plan is missing native mutation fields: "
                + ", ".join(missing)
            )
        if (
            not isinstance(program, str)
            or not isinstance(token, int)
            or isinstance(token, bool)
            or token < 0
        ):
            raise ChangeExecutionPreconditionError(
                "program_id must be text and token_offset must be a non-negative integer"
            )
        if not isinstance(expected, str) or not isinstance(replacement, str):
            raise ChangeExecutionPreconditionError(
                "expected and replacement operands must be text"
            )
        if analysis_values.keys() <= values.keys() and (
            not isinstance(analysis_digest, str)
            or len(analysis_digest) != 64
            or not isinstance(network_id, str)
            or not network_id
            or not isinstance(network_digest, str)
            or len(network_digest) != 64
        ):
            raise ChangeExecutionPreconditionError(
                "native change-target analysis and network identity are incomplete"
            )
        if "hardware_context_digest" in values and (
            not isinstance(hardware_context_digest, str)
            or len(hardware_context_digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in hardware_context_digest.casefold()
            )
        ):
            raise ChangeExecutionPreconditionError(
                "native hardware context digest is incomplete"
            )
        return values

    @staticmethod
    def _validate_verification_contract(transaction: ChangeTransaction) -> None:
        scope = transaction.plan.get("verification_scope")
        validation = transaction.plan.get("validation")
        manual_follow_up = transaction.plan.get("manual_follow_up")
        if scope != VERIFICATION_SCOPE:
            raise ChangeExecutionPreconditionError(
                f"unsupported verification_scope: {scope!r}"
            )
        if (
            not isinstance(validation, list)
            or tuple(validation) != OFFLINE_NATIVE_VALIDATION_STEPS
        ):
            raise ChangeExecutionPreconditionError(
                "offline_native_check_only requires exactly structural_diff, "
                "xg5000_program_check, and project_save"
            )
        if not isinstance(manual_follow_up, list) or any(
            not isinstance(item, str) or not item.strip()
            for item in manual_follow_up
        ):
            raise ChangeExecutionPreconditionError(
                "manual_follow_up must contain only non-empty text items"
            )

    @staticmethod
    def _verification_contract_evidence(
        transaction: ChangeTransaction,
    ) -> dict[str, Any]:
        MCPChangeExecutionService._validate_verification_contract(transaction)
        return {
            "verification_scope": VERIFICATION_SCOPE,
            "executed_checks": list(OFFLINE_NATIVE_VALIDATION_STEPS),
            "not_executed": list(NOT_EXECUTED_VALIDATION_TIERS),
            "manual_follow_up": list(transaction.plan["manual_follow_up"]),
        }

    @classmethod
    def _validate_mcp_plan_contract(
        cls,
        *,
        transaction: ChangeTransaction,
        plan_contract: dict[str, Any],
        plan_arguments: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> None:
        target = transaction.target
        expected_project_identity_digest = cls._project_identity_digest(target)
        expected_fields = {
            "session_id": target.engineering_session_id,
            "transaction_id": plan_arguments["transaction_id"],
            "workspace_id": target.workspace_id,
            "project_identity_digest": expected_project_identity_digest,
            "snapshot_id": plan_arguments["snapshot_id"],
            "backup_id": plan_arguments["backup_id"],
            "program_id": plan_arguments["program_id"],
            "token_offset": plan_arguments["token_offset"],
            "analysis_digest": plan_arguments["analysis_digest"],
            "hardware_context_digest": plan_arguments["hardware_context_digest"],
            "network_id": plan_arguments["network_id"],
            "network_digest": plan_arguments["network_digest"],
            "expected": plan_arguments["expected"],
            "replacement": plan_arguments["replacement"],
            "project_hash": target.artifact_hash,
        }
        for field_name, expected in expected_fields.items():
            if plan_contract.get(field_name) != expected:
                raise ChangeExecutionPreconditionError(
                    f"program.plan_change returned a mismatched {field_name}"
                )

        programs = snapshot.get("programs")
        if not isinstance(programs, list):
            raise ChangeExecutionPreconditionError(
                "project.snapshot did not return its program payload identities"
            )
        snapshot_program = next(
            (
                item
                for item in programs
                if isinstance(item, dict)
                and item.get("program_id") == plan_arguments["program_id"]
            ),
            None,
        )
        if snapshot_program is None:
            raise ChangeExecutionPreconditionError(
                "approved program_id is absent from the pinned snapshot"
            )
        expected_payload_hash = cls._required_sha256(
            snapshot_program,
            "payload_sha256",
            "project.snapshot",
        )
        if plan_contract.get("payload_sha256_before") != expected_payload_hash:
            raise ChangeExecutionPreconditionError(
                "program.plan_change returned a mismatched payload_sha256_before"
            )
        cls._required_sha256(
            plan_contract,
            "payload_sha256_planned",
            "program.plan_change",
        )
        returned_digest = cls._required_sha256(
            plan_contract,
            "plan_digest",
            "program.plan_change",
        )
        expected_digest = cls._mcp_plan_digest(plan_contract)
        if returned_digest != expected_digest:
            raise ChangeExecutionPreconditionError(
                "program.plan_change returned a plan_digest that does not bind "
                "the approved mutation contract"
            )

    @staticmethod
    def _project_identity_digest(target: ChangeTargetIdentity) -> str:
        contract = {
            "schema": "controlstudio-workspace-project-identity/v1",
            "workspace_id": str(uuid.UUID(target.workspace_id)),
            "working_copy_path": os.path.normcase(
                str(Path(target.project_path).expanduser().resolve())
            ),
            "source_sha256": MCPChangeExecutionService._normalize_sha256(
                target.source_artifact_hash,
                "target source_artifact_hash",
            ),
        }
        return hashlib.sha256(
            json.dumps(
                contract,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    @classmethod
    def _mcp_target_identity_digest(
        cls,
        *,
        session_id: str | None,
        pid: int | None,
        hwnd: int | None,
        project_path: str,
        project_hash: str,
        workspace_id: str,
        project_identity_digest: str,
    ) -> str:
        if not session_id or pid is None or hwnd is None or pid <= 0 or hwnd <= 0:
            raise ChangeExecutionPreconditionError(
                "mutation authorization requires an exact engineering target identity"
            )
        payload = {
            "schema": "controlstudio-mutation-target-identity/v1",
            "session_id": session_id,
            "pid": pid,
            "hwnd": hwnd,
            "project_path": os.path.normcase(str(Path(project_path).expanduser().resolve())),
            "project_hash": cls._normalize_sha256(project_hash, "target project_hash"),
            "workspace_id": str(uuid.UUID(workspace_id)),
            "project_identity_digest": cls._normalize_sha256(
                project_identity_digest,
                "project_identity_digest",
            ),
        }
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    @classmethod
    def _mcp_mutation_arguments_digest(
        cls,
        *,
        operation: str,
        arguments: dict[str, Any],
    ) -> str:
        if operation != "program.apply_ld_patch":
            raise ChangeExecutionPreconditionError("unsupported MCP mutation operation")
        transaction_id = str(arguments.get("transaction_id") or "").strip()
        if not transaction_id:
            raise ChangeExecutionPreconditionError(
                "mutation authorization requires a transaction id"
            )
        plan_id = str(arguments.get("plan_id") or "").strip()
        if not plan_id:
            raise ChangeExecutionPreconditionError(
                "mutation authorization requires a plan id"
            )
        payload = {
            "schema": "controlstudio-mutation-arguments/v1",
            "operation": operation,
            "transaction_id": transaction_id,
            "plan_id": plan_id,
            "plan_digest": cls._normalize_sha256(arguments.get("plan_digest"), "plan_digest"),
            "target_identity_digest": cls._mcp_target_identity_digest(
                session_id=arguments.get("session_id"),
                pid=arguments.get("pid"),
                hwnd=arguments.get("hwnd"),
                project_path=str(arguments.get("project_path") or ""),
                project_hash=str(arguments.get("project_hash") or ""),
                workspace_id=str(arguments.get("workspace_id") or ""),
                project_identity_digest=str(
                    arguments.get("project_identity_digest") or ""
                ),
            ),
        }
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    @classmethod
    def _mcp_plan_digest(cls, plan: dict[str, Any]) -> str:
        payload = {
            "schema": "controlstudio-ld-plan-digest/v1",
            "transaction_id": str(plan.get("transaction_id") or ""),
            "session_id": str(plan.get("session_id") or ""),
            "workspace_id": str(plan.get("workspace_id") or ""),
            "project_identity_digest": str(
                plan.get("project_identity_digest") or ""
            ),
            "snapshot_id": str(plan.get("snapshot_id") or ""),
            "backup_id": str(plan.get("backup_id") or ""),
            "plan_id": str(plan.get("plan_id") or ""),
            "program_id": str(plan.get("program_id") or ""),
            "token_offset": int(plan.get("token_offset", -1)),
            "expected": str(plan.get("expected") or ""),
            "replacement": str(plan.get("replacement") or ""),
            "payload_sha256_before": cls._normalize_sha256(
                plan.get("payload_sha256_before"),
                "payload_sha256_before",
            ),
            "payload_sha256_planned": cls._normalize_sha256(
                plan.get("payload_sha256_planned"),
                "payload_sha256_planned",
            ),
            "project_sha256_before": cls._normalize_sha256(
                plan.get("project_hash"),
                "project_sha256_before",
            ),
        }
        required = (
            "transaction_id",
            "session_id",
            "workspace_id",
            "project_identity_digest",
            "snapshot_id",
            "backup_id",
            "plan_id",
            "program_id",
            "expected",
            "replacement",
        )
        if any(not payload[field] for field in required):
            raise ChangeExecutionPreconditionError(
                "program.plan_change returned an incomplete digest contract"
            )
        analysis_values = {
            "analysis_digest": plan.get("analysis_digest"),
            "network_id": plan.get("network_id"),
            "network_digest": plan.get("network_digest"),
        }
        if any(value not in {None, ""} for value in analysis_values.values()):
            if not all(value not in {None, ""} for value in analysis_values.values()):
                raise ChangeExecutionPreconditionError(
                    "program.plan_change returned an incomplete target analysis identity"
                )
            payload.update(
                {
                    "schema": "controlstudio-ld-plan-digest/v2",
                    "analysis_digest": cls._normalize_sha256(
                        analysis_values["analysis_digest"],
                        "analysis_digest",
                    ),
                    "network_id": str(analysis_values["network_id"]),
                    "network_digest": cls._normalize_sha256(
                        analysis_values["network_digest"],
                        "network_digest",
                    ),
                }
            )
        hardware_context_digest = plan.get("hardware_context_digest")
        if hardware_context_digest not in {None, ""}:
            if not all(value not in {None, ""} for value in analysis_values.values()):
                raise ChangeExecutionPreconditionError(
                    "hardware-bound MCP plan omitted its target analysis identity"
                )
            payload.update(
                {
                    "schema": "controlstudio-ld-plan-digest/v3",
                    "hardware_context_digest": cls._normalize_sha256(
                        hardware_context_digest,
                        "hardware_context_digest",
                    ),
                }
            )
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _normalize_sha256(value: Any, label: str) -> str:
        normalized = str(value or "").strip().casefold()
        if len(normalized) != 64 or any(
            character not in "0123456789abcdef"
            for character in normalized
        ):
            raise ChangeExecutionPreconditionError(
                f"{label} must be a SHA-256 value"
            )
        return normalized

    @classmethod
    def _required_sha256(
        cls,
        value: dict[str, Any],
        key: str,
        operation: str,
    ) -> str:
        return cls._normalize_sha256(value.get(key), f"{operation}.{key}")

    @staticmethod
    def _session_scope(transaction: ChangeTransaction) -> dict[str, Any]:
        target = transaction.target
        return {
            "session_id": target.engineering_session_id,
            "pid": target.engineering_process_id,
            "hwnd": target.engineering_window_handle,
            "project_path": target.project_path,
            "project_hash": target.artifact_hash,
        }

    @staticmethod
    def _recovery_identity(
        transaction: ChangeTransaction,
    ) -> tuple[str, str] | None:
        candidates = [
            transaction.result.get("apply"),
            transaction.result.get("native_checkpoint"),
            (
                transaction.result.get("verification", {}).get("recovery")
                if isinstance(transaction.result.get("verification"), dict)
                else None
            ),
        ]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            transaction_id = candidate.get("mcp_transaction_id")
            plan_id = candidate.get("mcp_plan_id")
            if (
                isinstance(transaction_id, str)
                and transaction_id
                and isinstance(plan_id, str)
                and plan_id
            ):
                return transaction_id, plan_id
        return None

    def _persist_checkpoint(
        self,
        transaction: ChangeTransaction,
        checkpoint: dict[str, Any],
    ) -> None:
        if self._checkpoint_callback is None:
            return
        self._checkpoint_callback(
            transaction.id,
            {
                "host_transaction_id": transaction.id,
                "host_plan_digest": transaction.plan_digest,
                **checkpoint,
            },
        )

    async def _persist_checkpoint_or_abort(
        self,
        transaction: ChangeTransaction,
        checkpoint: dict[str, Any],
        *,
        session_scope: dict[str, Any],
        mcp_transaction_id: str,
        expected_mcp_state: str,
    ) -> None:
        try:
            self._persist_checkpoint(transaction, checkpoint)
            return
        except Exception as checkpoint_error:
            try:
                aborted = self._require_object(
                    await self._gateway.call_change_tool(
                        "change.abort",
                        {
                            **session_scope,
                            "transaction_id": mcp_transaction_id,
                            "expected_transaction_state": expected_mcp_state,
                        },
                    ),
                    "change.abort after Host checkpoint failure",
                )
                transaction_checkpoint = self._recovery_checkpoint(
                    aborted,
                    "transaction_checkpoint",
                    "change.abort after Host checkpoint failure",
                )
                if (
                    aborted.get("state_after") != "ABORTED_NO_MUTATION"
                    or aborted.get("native_mutation_performed") is not False
                    or aborted.get("lease_released") is not True
                    or transaction_checkpoint.get("host_transaction_id")
                    != transaction.id
                    or transaction_checkpoint.get("transaction_id")
                    != mcp_transaction_id
                    or transaction_checkpoint.get("state")
                    != "ABORTED_NO_MUTATION"
                ):
                    raise ValueError(
                        "MCP did not prove a terminal no-mutation abort"
                    )
            except (MCPToolError, ValueError, TypeError) as abort_error:
                raise ChangeExecutionOutcomeUncertain(
                    "Host checkpoint persistence failed and the native transaction "
                    "could not be proven aborted",
                    evidence={
                        "host_transaction_id": transaction.id,
                        "mcp_transaction_id": mcp_transaction_id,
                        "expected_mcp_state": expected_mcp_state,
                        "checkpoint_error": str(checkpoint_error),
                        "abort_error": str(abort_error),
                    },
                ) from checkpoint_error
            raise ChangeExecutionFailedSafely(
                "Host checkpoint persistence failed; MCP proved that no native "
                "mutation occurred and released its lease"
            ) from checkpoint_error

    @staticmethod
    def _stored_native_checkpoint(
        transaction: ChangeTransaction,
    ) -> dict[str, Any] | None:
        checkpoint = transaction.result.get("native_checkpoint")
        if not isinstance(checkpoint, dict):
            return None
        mcp_transaction_id = checkpoint.get("mcp_transaction_id")
        if not isinstance(mcp_transaction_id, str) or not mcp_transaction_id:
            return None
        return {
            "schema": "controlstudio-change-transaction-checkpoint/v1",
            "host_transaction_id": transaction.id,
            "transaction_id": mcp_transaction_id,
            "snapshot_id": checkpoint.get("snapshot_id"),
            "backup_id": checkpoint.get("backup_id"),
            "plan_id": checkpoint.get("mcp_plan_id"),
            "plan_digest": checkpoint.get("mcp_plan_digest"),
            "workspace_id": checkpoint.get("workspace_id"),
            "project_identity_digest": checkpoint.get(
                "project_identity_digest"
            ),
            "active_session_id": checkpoint.get("session_id"),
            "pid": transaction.target.engineering_process_id,
            "hwnd": transaction.target.engineering_window_handle,
            "project_path": checkpoint.get("project_path"),
            "current_project_hash": checkpoint.get("project_hash"),
            "state": checkpoint.get("stage"),
        }

    @staticmethod
    def _recovery_checkpoint(
        response: dict[str, Any],
        field: str,
        operation: str,
    ) -> dict[str, Any]:
        checkpoint = response.get(field)
        if not isinstance(checkpoint, dict):
            raise TypeError(f"{operation} returned no {field}")
        if checkpoint.get("schema") != (
            "controlstudio-change-transaction-checkpoint/v1"
        ):
            raise ValueError(f"{operation} returned an unsupported checkpoint schema")
        return checkpoint

    @classmethod
    def _validate_recovery_checkpoint(
        cls,
        *,
        transaction: ChangeTransaction,
        checkpoint: dict[str, Any],
        expected_transaction_id: str | None = None,
        expected_session_id: str | None = None,
        expected_pid: int | None = None,
        expected_hwnd: int | None = None,
        expected_project_hash: str | None = None,
    ) -> None:
        target = transaction.target
        exact = {
            "host_transaction_id": transaction.id,
            "workspace_id": target.workspace_id,
            "project_identity_digest": cls._project_identity_digest(target),
        }
        if expected_transaction_id is not None:
            exact["transaction_id"] = expected_transaction_id
        if expected_session_id is not None:
            exact["active_session_id"] = expected_session_id
        if expected_pid is not None:
            exact["pid"] = expected_pid
        if expected_hwnd is not None:
            exact["hwnd"] = expected_hwnd
        if expected_project_hash is not None:
            exact["current_project_hash"] = expected_project_hash
        for field_name, expected in exact.items():
            if checkpoint.get(field_name) != expected:
                raise ValueError(
                    f"native recovery checkpoint mismatched {field_name}"
                )
        project_path = checkpoint.get("project_path")
        if (
            not isinstance(project_path, str)
            or os.path.normcase(str(Path(project_path).resolve()))
            != os.path.normcase(str(Path(target.project_path).resolve()))
        ):
            raise ValueError("native recovery checkpoint mismatched project_path")
        state = checkpoint.get("state")
        action = checkpoint.get("recovery_action")
        if not isinstance(state, str) or not state:
            raise ValueError("native recovery checkpoint has no state")
        if action is not None and action not in {
            "abort_safe",
            "rollback_required",
            "already_terminal",
            "manual_quarantine",
        }:
            raise ValueError("native recovery checkpoint has invalid recovery_action")
        native_plan = cls._native_plan_arguments(transaction)
        has_native_plan = checkpoint.get("plan_id") is not None
        if action == "rollback_required" and (
            not has_native_plan or checkpoint.get("plan_digest") is None
        ):
            raise ValueError(
                "rollback-required checkpoint has no immutable native plan identity"
            )
        if has_native_plan:
            for field_name, expected in native_plan.items():
                if checkpoint.get(field_name) != expected:
                    raise ValueError(
                        f"native recovery checkpoint mismatched {field_name}"
                    )

    @classmethod
    def _validate_recovery_attach(
        cls,
        *,
        transaction: ChangeTransaction,
        recovery_target: ChangeRecoveryTarget,
        expected_project_hash: str,
        expected_transaction_id: str,
        attached: dict[str, Any],
    ) -> str:
        session_id = cls._required_text(
            attached,
            "session_id",
            "xg5000.attach recovery",
        )
        expected = {
            "pid": recovery_target.pid,
            "hwnd": recovery_target.hwnd,
            "project_hash": expected_project_hash,
        }
        for field_name, value in expected.items():
            if attached.get(field_name) != value:
                raise ValueError(f"recovery attach mismatched {field_name}")
        attached_path = attached.get("project_path")
        if (
            not isinstance(attached_path, str)
            or os.path.normcase(str(Path(attached_path).resolve()))
            != os.path.normcase(
                str(Path(transaction.target.project_path).resolve())
            )
        ):
            raise ValueError("recovery attach mismatched project_path")
        checkpoint = cls._recovery_checkpoint(
            attached,
            "recovery",
            "xg5000.attach recovery",
        )
        recovery_action = checkpoint.get("recovery_action")
        expected_policy = {
            "abort_safe": "offline_abort_recovery_only",
            "rollback_required": "offline_rollback_recovery_only",
        }.get(recovery_action)
        if expected_policy is None or attached.get("plc_policy") != expected_policy:
            raise ValueError("recovery attach returned a mismatched recovery policy")
        cls._validate_recovery_checkpoint(
            transaction=transaction,
            checkpoint=checkpoint,
            expected_transaction_id=expected_transaction_id,
            expected_session_id=session_id,
            expected_pid=recovery_target.pid,
            expected_hwnd=recovery_target.hwnd,
            expected_project_hash=expected_project_hash,
        )
        return session_id

    @staticmethod
    def _hash_project_file(project_path: str) -> str:
        path = Path(project_path).expanduser().resolve()
        if not path.is_file():
            raise OSError(f"recovery project file does not exist: {path}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    async def _authoritative_scope_after_save(
        self,
        *,
        transaction: ChangeTransaction,
        mcp_transaction_id: str,
        fallback: dict[str, Any],
        save_result: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        status = self._require_object(
            await self._gateway.call_change_tool(
                "change.transaction_status",
                {
                    "session_id": transaction.target.engineering_session_id,
                    "transaction_id": mcp_transaction_id,
                },
            ),
            "change.transaction_status",
        )
        native = status.get("transaction")
        if not isinstance(native, dict):
            raise TypeError("change.transaction_status returned no transaction")
        scope = dict(fallback)
        project_hash = native.get("current_project_hash")
        if not isinstance(project_hash, str) or len(project_hash) != 64:
            project_hash = self._find_project_hash(save_result)
        if not isinstance(project_hash, str) or len(project_hash) != 64:
            raise ValueError("authoritative saved project hash is unavailable")
        scope["project_hash"] = project_hash.casefold()
        mappings = {
            "active_session_id": "session_id",
            "active_pid": "pid",
            "active_hwnd": "hwnd",
            "project_path": "project_path",
        }
        for source, destination in mappings.items():
            value = native.get(source)
            if value is not None and value != "":
                scope[destination] = value
        self._validate_saved_status(
            transaction=transaction,
            status=status,
            mcp_transaction_id=mcp_transaction_id,
            expected_project_hash=scope["project_hash"],
        )
        return scope, status

    @classmethod
    def _validate_verified_operation(
        cls,
        *,
        result: dict[str, Any],
        operation: str,
        transaction: ChangeTransaction,
        mcp_transaction_id: str,
        expected_state: str,
        expected_project_hash: str | None = None,
    ) -> dict[str, Any]:
        if (
            result.get("ok") is not True
            or result.get("verified") is not True
            or result.get("operation") != operation
            or result.get("transaction_id") != mcp_transaction_id
            or result.get("transaction_state") != expected_state
        ):
            raise ValueError(
                f"{operation} did not prove the expected {expected_state} transaction"
            )
        checkpoint = cls._recovery_checkpoint(
            result,
            "transaction_checkpoint",
            operation,
        )
        cls._validate_recovery_checkpoint(
            transaction=transaction,
            checkpoint=checkpoint,
            expected_transaction_id=mcp_transaction_id,
            expected_session_id=transaction.target.engineering_session_id,
            expected_pid=transaction.target.engineering_process_id,
            expected_hwnd=transaction.target.engineering_window_handle,
            expected_project_hash=expected_project_hash,
        )
        if checkpoint.get("state") != expected_state:
            raise ValueError(f"{operation} checkpoint has the wrong state")
        return checkpoint

    @classmethod
    def _validate_diff_result(
        cls,
        *,
        transaction: ChangeTransaction,
        result: dict[str, Any],
        mcp_transaction_id: str,
        mcp_plan_id: str,
    ) -> None:
        cls._validate_verified_operation(
            result=result,
            operation="project.diff",
            transaction=transaction,
            mcp_transaction_id=mcp_transaction_id,
            expected_state="DIFF_VERIFIED",
            expected_project_hash=transaction.target.artifact_hash,
        )
        if (
            result.get("plan_id") != mcp_plan_id
            or result.get("matches_plan") is not True
            or result.get("matches_token_plan") is not True
            or result.get("matches_native_plan") is not True
            or result.get("project_unchanged") is not True
        ):
            raise ValueError("project.diff did not prove the exact approved native diff")

    @classmethod
    def _validate_validation_result(
        cls,
        *,
        transaction: ChangeTransaction,
        result: dict[str, Any],
        mcp_transaction_id: str,
        mcp_plan_id: str,
    ) -> None:
        cls._validate_verified_operation(
            result=result,
            operation="project.validate_change",
            transaction=transaction,
            mcp_transaction_id=mcp_transaction_id,
            expected_state="CHECKED",
            expected_project_hash=transaction.target.artifact_hash,
        )
        program_check = result.get("program_check")
        if (
            result.get("plan_id") != mcp_plan_id
            or result.get("matches_native_plan") is not True
            or result.get("status") != "validated"
            or result.get("rollback") is not None
            or result.get("project_unchanged") is not True
            or not isinstance(program_check, dict)
            or program_check.get("ok") is not True
            or program_check.get("verified") is not True
        ):
            raise ValueError(
                "project.validate_change did not prove native readback and program check"
            )

    @classmethod
    def _validate_save_result(
        cls,
        *,
        transaction: ChangeTransaction,
        result: dict[str, Any],
        mcp_transaction_id: str,
        expected_state: str,
    ) -> str:
        project_hash = cls._required_sha256(
            result,
            "project_hash",
            "project.commit_save",
        )
        checkpoint = cls._validate_verified_operation(
            result=result,
            operation="project.commit_save",
            transaction=transaction,
            mcp_transaction_id=mcp_transaction_id,
            expected_state=expected_state,
            expected_project_hash=project_hash,
        )
        project_path = result.get("project_path")
        if (
            not isinstance(project_path, str)
            or os.path.normcase(str(Path(project_path).resolve()))
            != os.path.normcase(str(Path(transaction.target.project_path).resolve()))
            or checkpoint.get("current_project_hash") != project_hash
        ):
            raise ValueError("project.commit_save did not prove the exact saved project")
        if expected_state == "ROLLED_BACK_SAVED" and (
            checkpoint.get("terminal") is not True
            or checkpoint.get("recovery_action") != "already_terminal"
        ):
            raise ValueError("rollback save is not a proven terminal recovery")
        return project_hash

    @classmethod
    def _validate_saved_status(
        cls,
        *,
        transaction: ChangeTransaction,
        status: dict[str, Any],
        mcp_transaction_id: str,
        expected_project_hash: str,
    ) -> None:
        if (
            status.get("ok") is not True
            or status.get("verified") is not True
            or status.get("operation") != "change.transaction_status"
        ):
            raise ValueError("change.transaction_status is not a verified status")
        native = status.get("transaction")
        if not isinstance(native, dict):
            raise TypeError("change.transaction_status returned no transaction")
        expected = {
            "transaction_id": mcp_transaction_id,
            "state": "SAVED",
            "active_session_id": transaction.target.engineering_session_id,
            "active_pid": transaction.target.engineering_process_id,
            "active_hwnd": transaction.target.engineering_window_handle,
            "current_project_hash": expected_project_hash,
        }
        for field_name, value in expected.items():
            if native.get(field_name) != value:
                raise ValueError(
                    f"change.transaction_status mismatched {field_name}"
                )
        native_path = native.get("project_path")
        if (
            not isinstance(native_path, str)
            or os.path.normcase(str(Path(native_path).resolve()))
            != os.path.normcase(str(Path(transaction.target.project_path).resolve()))
        ):
            raise ValueError("change.transaction_status mismatched project_path")
        checkpoint = cls._recovery_checkpoint(
            status,
            "transaction_checkpoint",
            "change.transaction_status",
        )
        cls._validate_recovery_checkpoint(
            transaction=transaction,
            checkpoint=checkpoint,
            expected_transaction_id=mcp_transaction_id,
            expected_session_id=transaction.target.engineering_session_id,
            expected_pid=transaction.target.engineering_process_id,
            expected_hwnd=transaction.target.engineering_window_handle,
            expected_project_hash=expected_project_hash,
        )
        if checkpoint.get("state") != "SAVED":
            raise ValueError("change.transaction_status checkpoint is not SAVED")

    @classmethod
    def _validate_commit_result(
        cls,
        *,
        transaction: ChangeTransaction,
        result: dict[str, Any],
        mcp_transaction_id: str,
        expected_project_hash: str,
    ) -> None:
        checkpoint = cls._validate_verified_operation(
            result=result,
            operation="change.commit",
            transaction=transaction,
            mcp_transaction_id=mcp_transaction_id,
            expected_state="COMMITTED",
            expected_project_hash=expected_project_hash,
        )
        if (
            result.get("project_hash") != expected_project_hash
            or result.get("project_unchanged") is not True
            or checkpoint.get("terminal") is not True
            or checkpoint.get("recovery_action") != "already_terminal"
            or checkpoint.get("lease_status") != "released"
        ):
            raise ValueError("change.commit did not prove terminal committed state")

    @classmethod
    def _validate_rollback_result(
        cls,
        *,
        transaction: ChangeTransaction,
        result: dict[str, Any],
        mcp_transaction_id: str,
        mcp_plan_id: str,
    ) -> None:
        cls._validate_verified_operation(
            result=result,
            operation="project.rollback_change",
            transaction=transaction,
            mcp_transaction_id=mcp_transaction_id,
            expected_state="ROLLED_BACK_EDITOR",
        )
        if (
            result.get("plan_id") != mcp_plan_id
            or result.get("status") != "rolled_back"
            or result.get("matches_original_canonical") is not True
            or result.get("matches_original_logic_and_nonvolatile") is not True
        ):
            raise ValueError("project.rollback_change did not prove native restoration")

    @classmethod
    def _find_project_hash(cls, value: Any) -> str | None:
        if not isinstance(value, dict):
            return None
        for key in ("project_hash", "hash_after", "current_project_hash"):
            candidate = value.get(key)
            if isinstance(candidate, str) and len(candidate) == 64:
                return candidate
        for candidate in value.values():
            found = cls._find_project_hash(candidate)
            if found is not None:
                return found
        return None

    @staticmethod
    def _require_object(value: Any, operation: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise TypeError(f"{operation} returned a non-object result")
        return value

    @staticmethod
    def _required_text(value: dict[str, Any], field: str, operation: str) -> str:
        item = value.get(field)
        if not isinstance(item, str) or not item:
            raise ValueError(f"{operation} did not return {field}")
        return item
