from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from .api_models import is_hardware_free_m_operand_target
from .mcp_client import MCPGateway, MCPToolError
from .models import (
    ChangeRiskLevel,
    ChangeTargetIdentity,
    ChangeTransaction,
    ChangeTransactionState,
)

VERIFICATION_SCOPE = "offline_native_reopen"
CHECK_ONLY_VERIFICATION_SCOPE = "offline_native_check_only"
OFFLINE_NATIVE_VALIDATION_STEPS = (
    "structural_diff",
    "xg5000_program_check",
    "project_save",
    "project_reopen",
)
OFFLINE_NATIVE_CHECK_ONLY_VALIDATION_STEPS = OFFLINE_NATIVE_VALIDATION_STEPS[:-1]
NOT_EXECUTED_VALIDATION_TIERS = (
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

    def __init__(
        self,
        message: str,
        *,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.evidence = evidence or {}


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
        required_native_fields = {
            "analysis_digest",
            "network_id",
            "network_digest",
        }
        if not self._hardware_free_m_plan(transaction):
            required_native_fields.add("hardware_context_digest")
        if not required_native_fields.issubset(native_plan):
            raise ChangeExecutionPreconditionError(
                "offline execution requires a native change-target analysis receipt"
            )
        self._validate_verification_contract(transaction)

    async def apply(self, transaction: ChangeTransaction) -> ChangeApplyResult:
        await self.preflight(transaction)
        target = transaction.target
        session_scope = self._session_scope(transaction)
        mcp_transaction_id: str | None = None
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
                    "payload_canonical_sha256_planned": self._required_sha256(
                        plan_contract,
                        "payload_canonical_sha256_planned",
                        "program.plan_change",
                    ),
                    "program_name": self._required_text(
                        plan_contract,
                        "program_name",
                        "program.plan_change",
                    ),
                    "native_replacement": self._required_text(
                        plan_contract,
                        "native_replacement",
                        "program.plan_change",
                    ),
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
            if mcp_transaction_id is not None:
                try:
                    await self._abort_failed_preapply_transaction(
                        transaction=transaction,
                        session_scope=session_scope,
                        mcp_transaction_id=mcp_transaction_id,
                    )
                except (MCPToolError, ValueError, TypeError) as abort_error:
                    raise ChangeExecutionOutcomeUncertain(
                        "Pre-apply execution failed and the no-mutation native "
                        "transaction could not be proven aborted",
                        evidence={
                            "mcp_transaction_id": mcp_transaction_id,
                            "preapply_error": str(exc),
                            "abort_error": str(abort_error),
                        },
                    ) from exc
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
        except (
            MCPToolError,
            ChangeExecutionPreconditionError,
            ValueError,
            TypeError,
        ) as exc:
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
                "mcp_plan": plan_contract,
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
        mcp_plan_digest = self._required_sha256(
            evidence,
            "mcp_plan_digest",
            "apply evidence",
        )
        mcp_plan = self._require_object(evidence.get("mcp_plan"), "apply MCP plan")
        expected_ld_payload_sha256 = self._required_sha256(
            mcp_plan,
            "payload_sha256_planned",
            "apply MCP plan",
        )
        session_scope = self._session_scope(transaction)
        verification = self._verification_contract_evidence(transaction)
        check_save_only = (
            transaction.plan.get("verification_scope")
            == CHECK_ONLY_VERIFICATION_SCOPE
        )
        # The direct-LD reopen command closes the source editor before it can
        # attach the independently opened target.  Once invoked, the original
        # HWND is not a valid rollback target; any failure is uncertain.
        fresh_reopen_attempted = False
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
                mcp_plan_id=mcp_plan_id,
                mcp_plan_digest=mcp_plan_digest,
                fallback=session_scope,
            )
            verification["saved_transaction_status"] = saved_status
            if check_save_only:
                commit_result = self._require_object(
                    await self._gateway.call_change_tool(
                        "change.commit",
                        {
                            **session_scope,
                            "transaction_id": mcp_transaction_id,
                            "host_transaction_id": transaction.id,
                            "plan_id": mcp_plan_id,
                            "expected_plan_digest": mcp_plan_digest,
                            "verification_scope": CHECK_ONLY_VERIFICATION_SCOPE,
                        },
                    ),
                    "change.commit",
                )
                self._validate_commit_result(
                    transaction=transaction,
                    result=commit_result,
                    mcp_transaction_id=mcp_transaction_id,
                    expected_project_hash=session_scope["project_hash"],
                    expected_session_scope=session_scope,
                )
                verification["commit"] = commit_result
            else:
                fresh_reopen_result_digest = self._ld_fresh_reopen_result_digest(
                    transaction=transaction,
                    mcp_transaction_id=mcp_transaction_id,
                    mcp_plan_id=mcp_plan_id,
                    mcp_plan_digest=mcp_plan_digest,
                    saved_project_hash=session_scope["project_hash"],
                    apply_evidence=evidence.get("apply"),
                )
                fresh_reopen_attempted = True
                fresh_reopen = self._require_object(
                    await self._gateway.call_change_tool(
                        "project.verify_ld_operand_reopen",
                        {
                            **session_scope,
                            "transaction_id": mcp_transaction_id,
                            "plan_id": mcp_plan_id,
                            "approval_id": transaction.id,
                            "approval_plan_digest": transaction.plan_digest,
                            "result_digest": fresh_reopen_result_digest,
                            "timeout_seconds": 120.0,
                        },
                    ),
                    "project.verify_ld_operand_reopen",
                )
                fresh_scope = self._validate_ld_fresh_reopen_result(
                    transaction=transaction,
                    result=fresh_reopen,
                    mcp_transaction_id=mcp_transaction_id,
                    mcp_plan_id=mcp_plan_id,
                    mcp_plan_digest=mcp_plan_digest,
                    saved_project_hash=session_scope["project_hash"],
                    result_digest=fresh_reopen_result_digest,
                    expected_ld_payload_sha256=expected_ld_payload_sha256,
                )
                verification["fresh_reopen"] = fresh_reopen
                attached = self._require_object(
                    await self._gateway.call_read_tool(
                        "xg5000.attach",
                        {
                            "pid": fresh_scope["pid"],
                            "hwnd": fresh_scope["hwnd"],
                            "access_profile": "analysis",
                            "project_path": fresh_scope["project_path"],
                            "project_hash": fresh_scope["project_hash"],
                            "workspace_id": transaction.target.workspace_id,
                            "reopen_from_session_id": session_scope["session_id"],
                        },
                    ),
                    "xg5000.attach fresh LD reopen",
                )
                session_scope = self._validate_ld_fresh_reopen_attach(
                    transaction=transaction,
                    attached=attached,
                    fresh_scope=fresh_scope,
                )
                self._gateway.bind_agent_session(session_scope)
                verification["fresh_reopen_attach"] = dict(attached)
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
                    expected_session_scope=session_scope,
                )
                verification["commit"] = commit_result
        except (
            MCPToolError,
            ChangeExecutionPreconditionError,
            ValueError,
            TypeError,
        ) as verification_error:
            if fresh_reopen_attempted:
                return ChangeVerificationResult(
                    state=ChangeTransactionState.QUARANTINED,
                    evidence={
                        **verification,
                        "verification_error": str(verification_error),
                        "fresh_reopen_attempted": True,
                    },
                    error_code="fresh_reopen_outcome_uncertain",
                    error_detail=(
                        "The source editor may be closed after save, but the "
                        "fresh LD reopen receipt could not be proven."
                    ),
                )
            try:
                recovery_scope, recovery_status = (
                    await self._authoritative_scope_for_rollback(
                        transaction=transaction,
                        mcp_transaction_id=mcp_transaction_id,
                        mcp_plan_id=mcp_plan_id,
                        mcp_plan_digest=mcp_plan_digest,
                        fallback=session_scope,
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
                    "offline_project_program_checked_and_saved"
                    if check_save_only
                    else "offline_project_saved_and_independently_reopened"
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
                    "program_name": "program_name",
                    "token_offset": "token_offset",
                    "expected": "expected",
                    "replacement": "replacement",
                    "native_replacement": "native_replacement",
                    "payload_canonical_sha256_planned": (
                        "payload_canonical_sha256_planned"
                    ),
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
        try:
            if checkpoint.get("lease_status") == "live":
                # A live native lease belongs to the already-bound MCP session.
                # Reattaching it is both unnecessary and rejected by the MCP because
                # it could create a second recovery authority for the same editor.
                recovery_session_id = self._required_text(
                    checkpoint,
                    "active_session_id",
                    "live native recovery checkpoint",
                )
                if (
                    checkpoint.get("pid") != recovery_target.pid
                    or checkpoint.get("hwnd") != recovery_target.hwnd
                    or checkpoint.get("current_project_hash") != current_project_hash
                ):
                    raise ChangeExecutionPreconditionError(
                        "live native recovery target differs from its durable checkpoint"
                    )
                attached = {
                    "mode": "existing_bound_session",
                    "session_id": recovery_session_id,
                    "pid": recovery_target.pid,
                    "hwnd": recovery_target.hwnd,
                    "project_path": target.project_path,
                    "project_hash": current_project_hash,
                }
            else:
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
                if (
                    isinstance(expected_native_plan_digest, str)
                    and expected_native_plan_digest
                ):
                    attach_arguments["recover_expected_plan_digest"] = (
                        expected_native_plan_digest
                    )
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
        except (
            MCPToolError,
            ChangeExecutionPreconditionError,
            ValueError,
            TypeError,
        ) as exc:
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
        mcp_plan_digest = self._required_sha256(
            checkpoint,
            "plan_digest",
            "change.transaction_status",
        )
        # A legacy LD plan may have reviewed P0021 while XG5000 correctly
        # persisted its measured native form P00021.  Let MCP promote only an
        # exact, single-token, canonical native-contract match out of
        # quarantine; every other uncertain outcome continues to rollback.
        native_contract_promoted = False
        reconciliation_fresh_reopen_attempted = False
        try:
            reconciled_diff = self._require_object(
                await self._gateway.call_change_tool(
                    "project.diff",
                    {
                        **recovery_scope,
                        "transaction_id": mcp_transaction_id,
                        "plan_id": mcp_plan_id,
                    },
                ),
                "project.diff native-contract reconciliation",
            )
            self._validate_diff_result(
                transaction=transaction,
                result=reconciled_diff,
                mcp_transaction_id=mcp_transaction_id,
                mcp_plan_id=mcp_plan_id,
            )
            if reconciled_diff.get("native_contract_reconciliation") is not True:
                raise ValueError(
                    "project.diff did not identify a quarantined native-contract match"
                )
            native_contract_promoted = True
            evidence["native_contract_reconciliation"] = reconciled_diff
            validation_result = self._require_object(
                await self._gateway.call_change_tool(
                    "project.validate_change",
                    {
                        **recovery_scope,
                        "transaction_id": mcp_transaction_id,
                        "plan_id": mcp_plan_id,
                        "rollback_after_validation": False,
                    },
                ),
                "project.validate_change after native-contract reconciliation",
            )
            self._validate_validation_result(
                transaction=transaction,
                result=validation_result,
                mcp_transaction_id=mcp_transaction_id,
                mcp_plan_id=mcp_plan_id,
            )
            evidence["validation"] = validation_result
            save_result = self._require_object(
                await self._gateway.call_change_tool(
                    "project.commit_save",
                    {
                        **recovery_scope,
                        "transaction_id": mcp_transaction_id,
                    },
                ),
                "project.commit_save after native-contract reconciliation",
            )
            self._validate_save_result(
                transaction=transaction,
                result=save_result,
                mcp_transaction_id=mcp_transaction_id,
                expected_state="SAVED",
            )
            evidence["save"] = save_result
            saved_scope, saved_status = await self._authoritative_scope_after_save(
                transaction=transaction,
                mcp_transaction_id=mcp_transaction_id,
                mcp_plan_id=mcp_plan_id,
                mcp_plan_digest=mcp_plan_digest,
                fallback=recovery_scope,
            )
            evidence["saved_transaction_status"] = saved_status
            if (
                transaction.plan.get("verification_scope")
                == CHECK_ONLY_VERIFICATION_SCOPE
            ):
                commit_result = self._require_object(
                    await self._gateway.call_change_tool(
                        "change.commit",
                        {
                            **saved_scope,
                            "transaction_id": mcp_transaction_id,
                            "host_transaction_id": transaction.id,
                            "plan_id": mcp_plan_id,
                            "expected_plan_digest": mcp_plan_digest,
                            "verification_scope": CHECK_ONLY_VERIFICATION_SCOPE,
                        },
                    ),
                    "change.commit after native-contract reconciliation",
                )
                self._validate_commit_result(
                    transaction=transaction,
                    result=commit_result,
                    mcp_transaction_id=mcp_transaction_id,
                    expected_project_hash=saved_scope["project_hash"],
                    expected_session_scope=saved_scope,
                )
                evidence["commit"] = commit_result
                return ChangeVerificationResult(
                    state=ChangeTransactionState.COMPLETED,
                    evidence=evidence,
                )
            result_digest = self._ld_fresh_reopen_result_digest(
                transaction=transaction,
                mcp_transaction_id=mcp_transaction_id,
                mcp_plan_id=mcp_plan_id,
                mcp_plan_digest=mcp_plan_digest,
                saved_project_hash=saved_scope["project_hash"],
                apply_evidence={"native_contract_reconciliation": reconciled_diff},
            )
            reconciliation_fresh_reopen_attempted = True
            fresh_reopen = self._require_object(
                await self._gateway.call_change_tool(
                    "project.verify_ld_operand_reopen",
                    {
                        **saved_scope,
                        "transaction_id": mcp_transaction_id,
                        "plan_id": mcp_plan_id,
                        "approval_id": transaction.id,
                        "approval_plan_digest": transaction.plan_digest,
                        "result_digest": result_digest,
                        "timeout_seconds": 120.0,
                    },
                ),
                "project.verify_ld_operand_reopen after reconciliation",
            )
            expected_payload_sha256 = self._required_sha256(
                self._require_object(reconciled_diff.get("diff"), "reconciled diff"),
                "payload_sha256_after",
                "reconciled diff",
            )
            fresh_scope = self._validate_ld_fresh_reopen_result(
                transaction=transaction,
                result=fresh_reopen,
                mcp_transaction_id=mcp_transaction_id,
                mcp_plan_id=mcp_plan_id,
                mcp_plan_digest=mcp_plan_digest,
                saved_project_hash=saved_scope["project_hash"],
                result_digest=result_digest,
                expected_ld_payload_sha256=expected_payload_sha256,
            )
            evidence["fresh_reopen"] = fresh_reopen
            fresh_attach = self._require_object(
                await self._gateway.call_read_tool(
                    "xg5000.attach",
                    {
                        "pid": fresh_scope["pid"],
                        "hwnd": fresh_scope["hwnd"],
                        "access_profile": "analysis",
                        "project_path": fresh_scope["project_path"],
                        "project_hash": fresh_scope["project_hash"],
                        "workspace_id": transaction.target.workspace_id,
                        "reopen_from_session_id": saved_scope["session_id"],
                    },
                ),
                "xg5000.attach fresh reconciled LD reopen",
            )
            fresh_session_scope = self._validate_ld_fresh_reopen_attach(
                transaction=transaction,
                attached=fresh_attach,
                fresh_scope=fresh_scope,
            )
            self._gateway.bind_agent_session(fresh_session_scope)
            evidence["fresh_reopen_attach"] = fresh_attach
            commit_result = self._require_object(
                await self._gateway.call_change_tool(
                    "change.commit",
                    {
                        **fresh_session_scope,
                        "transaction_id": mcp_transaction_id,
                    },
                ),
                "change.commit after native-contract reconciliation",
            )
            self._validate_commit_result(
                transaction=transaction,
                result=commit_result,
                mcp_transaction_id=mcp_transaction_id,
                expected_project_hash=fresh_session_scope["project_hash"],
                expected_session_scope=fresh_session_scope,
            )
            evidence["commit"] = commit_result
            return ChangeVerificationResult(
                state=ChangeTransactionState.COMPLETED,
                evidence=evidence,
            )
        except (MCPToolError, ValueError, TypeError) as reconcile_error:
            evidence["native_contract_reconciliation_error"] = str(reconcile_error)
            if native_contract_promoted:
                return ChangeVerificationResult(
                    state=ChangeTransactionState.QUARANTINED,
                    evidence=evidence,
                    error_code=(
                        "fresh_reopen_outcome_uncertain"
                        if reconciliation_fresh_reopen_attempted
                        else "native_contract_continuation_unproven"
                    ),
                    error_detail=(
                        "The native-contract match was proven, but its remaining "
                        "validation lifecycle did not complete."
                    ),
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

    @staticmethod
    def _ld_fresh_reopen_result_digest(
        *,
        transaction: ChangeTransaction,
        mcp_transaction_id: str,
        mcp_plan_id: str,
        mcp_plan_digest: str,
        saved_project_hash: str,
        apply_evidence: object,
    ) -> str:
        """Bind the fresh-process receipt to the approved saved LD result."""
        payload = {
            "schema": "controlstudio-ld-fresh-reopen-result/v1",
            "host_transaction_id": transaction.id,
            "host_plan_digest": transaction.plan_digest,
            "mcp_transaction_id": mcp_transaction_id,
            "mcp_plan_id": mcp_plan_id,
            "mcp_plan_digest": mcp_plan_digest,
            "saved_project_hash": saved_project_hash,
            "apply_evidence": apply_evidence,
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
    def _validate_ld_fresh_reopen_result(
        cls,
        *,
        transaction: ChangeTransaction,
        result: dict[str, Any],
        mcp_transaction_id: str,
        mcp_plan_id: str,
        mcp_plan_digest: str,
        saved_project_hash: str,
        result_digest: str,
        expected_ld_payload_sha256: str,
    ) -> dict[str, Any]:
        fresh = result.get("fresh_reopen")
        readback = result.get("program_readback")
        approval = result.get("approval_binding")
        program_id = transaction.plan.get("target", {}).get(
            "program_id", transaction.plan.get("target", {}).get("programId")
        )
        program_name = transaction.plan.get("target", {}).get(
            "program_name", transaction.plan.get("target", {}).get("programName")
        )
        apply = result.get("program_check")
        if (
            result.get("ok") is not True
            or result.get("verified") is not True
            or result.get("operation") != "project.verify_ld_operand_reopen"
            or result.get("transaction_id") != mcp_transaction_id
            or result.get("project_unchanged") is not True
            or not isinstance(fresh, dict)
            or fresh.get("source_pid") != transaction.target.engineering_process_id
            or fresh.get("source_hwnd") != transaction.target.engineering_window_handle
            or fresh.get("source_closed") is not True
            or not isinstance(fresh.get("pid"), int)
            or not isinstance(fresh.get("hwnd"), int)
            or fresh["pid"] <= 0
            or fresh["hwnd"] <= 0
            or fresh["pid"] == transaction.target.engineering_process_id
            or fresh["hwnd"] == transaction.target.engineering_window_handle
            or not isinstance(fresh.get("process_create_time"), (int, float))
            or isinstance(fresh.get("process_create_time"), bool)
            or float(fresh["process_create_time"]) <= 0
            or fresh.get("workspace_id") != transaction.target.workspace_id
            or fresh.get("project_path") != transaction.target.project_path
            or fresh.get("project_sha256") != saved_project_hash
            or not isinstance(readback, dict)
            or readback.get("program_id") != program_id
            or readback.get("program_name") != program_name
            or readback.get("ld_payload_sha256") != expected_ld_payload_sha256
            or readback.get("stable_repeat") is not True
            or readback.get("final_view") != "LD"
            or not isinstance(approval, dict)
            or approval.get("approval_id") != transaction.id
            or approval.get("approval_plan_digest") != transaction.plan_digest
            or approval.get("result_digest") != result_digest
            or not isinstance(apply, dict)
            or apply.get("ok") is not True
            or apply.get("verified") is not True
            or mcp_plan_id == ""
            or cls._normalize_sha256(mcp_plan_digest, "mcp_plan_digest") != mcp_plan_digest
        ):
            raise ChangeExecutionPreconditionError(
                "fresh LD reopen receipt differs from the approved saved transaction"
            )
        payload_after = cls._required_sha256(
            readback,
            "ld_payload_sha256",
            "fresh LD reopen readback",
        )
        return {
            "session_id": "",
            "pid": fresh["pid"],
            "hwnd": fresh["hwnd"],
            "project_path": fresh["project_path"],
            "project_hash": fresh["project_sha256"],
            "ld_payload_sha256": payload_after,
        }

    @staticmethod
    def _validate_ld_fresh_reopen_attach(
        *,
        transaction: ChangeTransaction,
        attached: dict[str, Any],
        fresh_scope: dict[str, Any],
    ) -> dict[str, Any]:
        session = attached.get("session") if isinstance(attached.get("session"), dict) else attached
        if (
            not isinstance(session, dict)
            or not isinstance(session.get("session_id"), str)
            or not session["session_id"]
            or session.get("pid") != fresh_scope["pid"]
            or session.get("hwnd") != fresh_scope["hwnd"]
            or session.get("project_path") != fresh_scope["project_path"]
            or session.get("project_hash") != fresh_scope["project_hash"]
            or session.get("stage") != "SAVED_REOPENED_UNVERIFIED"
            or not isinstance(session.get("metadata"), dict)
            or session["metadata"].get("active_change_transaction_id") is None
        ):
            raise ChangeExecutionPreconditionError(
                "fresh LD reopen attach did not bind the new exact engineering session"
            )
        return {
            "session_id": session["session_id"],
            "pid": session["pid"],
            "hwnd": session["hwnd"],
            "project_path": session["project_path"],
            "project_hash": session["project_hash"],
        }

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
    def _hardware_free_m_plan(transaction: ChangeTransaction) -> bool:
        plan = transaction.plan
        target = plan.get("target")
        current = plan.get("current")
        proposed = plan.get("proposed")
        if not all(isinstance(value, dict) for value in (target, current, proposed)):
            return False
        assert isinstance(target, dict)
        assert isinstance(current, dict)
        assert isinstance(proposed, dict)
        return (
            target.get("hardware_context_binding") is None
            and target.get("hardwareContextBinding") is None
            and is_hardware_free_m_operand_target(
                target,
                current,
                proposed,
            )
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
        if scope not in {VERIFICATION_SCOPE, CHECK_ONLY_VERIFICATION_SCOPE}:
            raise ChangeExecutionPreconditionError(
                f"unsupported verification_scope: {scope!r}"
            )
        expected_steps = (
            OFFLINE_NATIVE_CHECK_ONLY_VALIDATION_STEPS
            if scope == CHECK_ONLY_VERIFICATION_SCOPE
            else OFFLINE_NATIVE_VALIDATION_STEPS
        )
        if (
            not isinstance(validation, list)
            or tuple(validation) != expected_steps
        ):
            raise ChangeExecutionPreconditionError(
                f"{scope} requires " + ", ".join(expected_steps) + " in that order"
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
        scope = str(transaction.plan["verification_scope"])
        executed_checks = (
            OFFLINE_NATIVE_CHECK_ONLY_VALIDATION_STEPS
            if scope == CHECK_ONLY_VERIFICATION_SCOPE
            else OFFLINE_NATIVE_VALIDATION_STEPS
        )
        return {
            "verification_scope": scope,
            "executed_checks": list(executed_checks),
            "not_executed": [
                *(
                    ["project_reopen"]
                    if scope == CHECK_ONLY_VERIFICATION_SCOPE
                    else []
                ),
                *NOT_EXECUTED_VALIDATION_TIERS,
            ],
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
            "network_id": plan_arguments["network_id"],
            "network_digest": plan_arguments["network_digest"],
            "expected": plan_arguments["expected"],
            "replacement": plan_arguments["replacement"],
            "project_hash": target.artifact_hash,
        }
        if "hardware_context_digest" in plan_arguments:
            expected_fields["hardware_context_digest"] = plan_arguments[
                "hardware_context_digest"
            ]
        elif plan_contract.get("hardware_context_digest") not in {None, ""}:
            raise ChangeExecutionPreconditionError(
                "program.plan_change unexpectedly returned hardware context authority"
            )
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
        program_name = cls._required_text(
            snapshot_program,
            "name",
            "project.snapshot program",
        )
        if plan_contract.get("program_name") != program_name:
            raise ChangeExecutionPreconditionError(
                "program.plan_change returned a mismatched program_name"
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
        cls._required_sha256(
            plan_contract,
            "payload_canonical_sha256_planned",
            "program.plan_change",
        )
        cls._required_text(
            plan_contract,
            "native_replacement",
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
        payload_canonical_sha256_planned = plan.get(
            "payload_canonical_sha256_planned"
        )
        if payload_canonical_sha256_planned not in {None, ""}:
            payload.update(
                {
                    "schema": "controlstudio-ld-plan-digest/v4",
                    "program_name": str(plan.get("program_name") or ""),
                    "native_replacement": str(
                        plan.get("native_replacement") or ""
                    ),
                    "payload_canonical_sha256_planned": cls._normalize_sha256(
                        payload_canonical_sha256_planned,
                        "payload_canonical_sha256_planned",
                    ),
                }
            )
            if not payload["program_name"] or not payload["native_replacement"]:
                raise ChangeExecutionPreconditionError(
                    "canonical MCP plan omitted program_name or native_replacement"
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
            "program_id": checkpoint.get("program_id"),
            "program_name": checkpoint.get("program_name"),
            "token_offset": checkpoint.get("token_offset"),
            "expected": checkpoint.get("expected"),
            "replacement": checkpoint.get("replacement"),
            "native_replacement": checkpoint.get("native_replacement"),
            "payload_canonical_sha256_planned": checkpoint.get(
                "payload_canonical_sha256_planned"
            ),
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
        mcp_plan_id: str,
        mcp_plan_digest: str,
        fallback: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        scope, status, _native, _checkpoint = (
            await self._authoritative_transaction_scope(
                transaction=transaction,
                mcp_transaction_id=mcp_transaction_id,
                mcp_plan_id=mcp_plan_id,
                mcp_plan_digest=mcp_plan_digest,
                fallback=fallback,
            )
        )
        self._validate_saved_status(
            transaction=transaction,
            status=status,
            mcp_transaction_id=mcp_transaction_id,
            expected_project_hash=scope["project_hash"],
        )
        return scope, status

    async def _abort_failed_preapply_transaction(
        self,
        *,
        transaction: ChangeTransaction,
        session_scope: dict[str, Any],
        mcp_transaction_id: str,
    ) -> None:
        """Close and release an exact transaction that failed before apply."""

        status = self._require_object(
            await self._gateway.call_change_tool(
                "change.transaction_status",
                {
                    "session_id": session_scope["session_id"],
                    "transaction_id": mcp_transaction_id,
                },
            ),
            "change.transaction_status after pre-apply failure",
        )
        if (
            status.get("ok") is not True
            or status.get("verified") is not True
            or status.get("operation") != "change.transaction_status"
        ):
            raise ValueError("pre-apply transaction status is not verified")
        checkpoint = self._recovery_checkpoint(
            status,
            "transaction_checkpoint",
            "change.transaction_status after pre-apply failure",
        )
        self._validate_recovery_checkpoint(
            transaction=transaction,
            checkpoint=checkpoint,
            expected_transaction_id=mcp_transaction_id,
            expected_session_id=session_scope["session_id"],
            expected_pid=session_scope["pid"],
            expected_hwnd=session_scope["hwnd"],
            expected_project_hash=session_scope["project_hash"],
        )
        state = checkpoint.get("state")
        if (
            state not in {"SNAPSHOT_CREATED", "BACKUP_CREATED", "PLANNED"}
            or checkpoint.get("recovery_action") != "abort_safe"
            or checkpoint.get("mutation_possible") is not False
        ):
            raise ValueError("failed pre-apply transaction is not safely abortable")
        aborted = self._require_object(
            await self._gateway.call_change_tool(
                "change.abort",
                {
                    **session_scope,
                    "transaction_id": mcp_transaction_id,
                    "expected_transaction_state": state,
                },
            ),
            "change.abort after pre-apply failure",
        )
        aborted_checkpoint = self._recovery_checkpoint(
            aborted,
            "transaction_checkpoint",
            "change.abort after pre-apply failure",
        )
        self._validate_recovery_checkpoint(
            transaction=transaction,
            checkpoint=aborted_checkpoint,
            expected_transaction_id=mcp_transaction_id,
            expected_session_id=session_scope["session_id"],
            expected_pid=session_scope["pid"],
            expected_hwnd=session_scope["hwnd"],
            expected_project_hash=session_scope["project_hash"],
        )
        if (
            aborted.get("state_after") != "ABORTED_NO_MUTATION"
            or aborted.get("native_mutation_performed") is not False
            or aborted.get("lease_released") is not True
            or aborted_checkpoint.get("state") != "ABORTED_NO_MUTATION"
            or aborted_checkpoint.get("terminal") is not True
            or aborted_checkpoint.get("lease_status") != "released"
        ):
            raise ValueError("pre-apply abort did not prove terminal lease release")

    async def _authoritative_transaction_scope(
        self,
        *,
        transaction: ChangeTransaction,
        mcp_transaction_id: str,
        mcp_plan_id: str,
        mcp_plan_digest: str,
        fallback: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
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
        if (
            status.get("ok") is not True
            or status.get("verified") is not True
            or status.get("operation") != "change.transaction_status"
        ):
            raise ValueError("change.transaction_status is not verified")
        native = status.get("transaction")
        if not isinstance(native, dict):
            raise TypeError("change.transaction_status returned no transaction")
        expected_identity = {
            "transaction_id": mcp_transaction_id,
            "active_session_id": transaction.target.engineering_session_id,
            "active_pid": transaction.target.engineering_process_id,
            "active_hwnd": transaction.target.engineering_window_handle,
        }
        for field_name, expected in expected_identity.items():
            if native.get(field_name) != expected:
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
        project_hash = self._normalize_sha256(
            native.get("current_project_hash"),
            "change.transaction_status current_project_hash",
        )
        checkpoint = self._recovery_checkpoint(
            status,
            "transaction_checkpoint",
            "change.transaction_status",
        )
        self._validate_recovery_checkpoint(
            transaction=transaction,
            checkpoint=checkpoint,
            expected_transaction_id=mcp_transaction_id,
            expected_session_id=transaction.target.engineering_session_id,
            expected_pid=transaction.target.engineering_process_id,
            expected_hwnd=transaction.target.engineering_window_handle,
            expected_project_hash=project_hash,
        )
        if (
            checkpoint.get("plan_id") != mcp_plan_id
            or checkpoint.get("plan_digest") != mcp_plan_digest
        ):
            raise ValueError("native transaction changed its immutable plan identity")
        if checkpoint.get("state") != native.get("state"):
            raise ValueError("native transaction and checkpoint states differ")
        scope = dict(fallback)
        scope.update(
            {
                "session_id": native["active_session_id"],
                "pid": native["active_pid"],
                "hwnd": native["active_hwnd"],
                "project_path": native_path,
                "project_hash": project_hash,
            }
        )
        return scope, status, native, checkpoint

    async def _authoritative_scope_for_rollback(
        self,
        *,
        transaction: ChangeTransaction,
        mcp_transaction_id: str,
        mcp_plan_id: str,
        mcp_plan_digest: str,
        fallback: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Bind rollback to the live native journal before or after save.

        Verification may fail while the native transaction is still APPLIED or
        DIFF_VERIFIED.  Reusing the success-path SAVED validator here prevented
        the separately journaled rollback from ever running for those states.
        """

        scope, status, native, checkpoint = (
            await self._authoritative_transaction_scope(
                transaction=transaction,
                mcp_transaction_id=mcp_transaction_id,
                mcp_plan_id=mcp_plan_id,
                mcp_plan_digest=mcp_plan_digest,
                fallback=fallback,
            )
        )
        native_state = native.get("state")
        rollback_states = {
            "APPLYING",
            "APPLIED",
            "DIFF_VERIFIED",
            "CHECKED",
            "SAVED",
            "QUARANTINED",
        }
        if native_state not in rollback_states:
            raise ValueError(
                f"native transaction state {native_state!r} is not rollback-capable"
            )
        if checkpoint.get("recovery_action") != "rollback_required":
            raise ValueError("native transaction did not authorize exact rollback")
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
        expected_session_id: str | None = None,
        expected_pid: int | None = None,
        expected_hwnd: int | None = None,
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
            expected_session_id=(
                expected_session_id or transaction.target.engineering_session_id
            ),
            expected_pid=(
                expected_pid if expected_pid is not None else transaction.target.engineering_process_id
            ),
            expected_hwnd=(
                expected_hwnd if expected_hwnd is not None else transaction.target.engineering_window_handle
            ),
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
        expected_session_scope: Mapping[str, Any] | None = None,
    ) -> None:
        session_scope = expected_session_scope or {}
        cls._validate_verified_operation(
            result=result,
            operation="project.diff",
            transaction=transaction,
            mcp_transaction_id=mcp_transaction_id,
            expected_state="DIFF_VERIFIED",
            expected_project_hash=transaction.target.artifact_hash,
            expected_session_id=session_scope.get("session_id"),
            expected_pid=session_scope.get("pid"),
            expected_hwnd=session_scope.get("hwnd"),
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
        expected_session_scope: Mapping[str, Any] | None = None,
    ) -> None:
        session_scope = expected_session_scope or {}
        cls._validate_verified_operation(
            result=result,
            operation="project.validate_change",
            transaction=transaction,
            mcp_transaction_id=mcp_transaction_id,
            expected_state="CHECKED",
            expected_project_hash=transaction.target.artifact_hash,
            expected_session_id=session_scope.get("session_id"),
            expected_pid=session_scope.get("pid"),
            expected_hwnd=session_scope.get("hwnd"),
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
        expected_session_scope: dict[str, Any],
    ) -> None:
        checkpoint = cls._validate_verified_operation(
            result=result,
            operation="change.commit",
            transaction=transaction,
            mcp_transaction_id=mcp_transaction_id,
            expected_state="COMMITTED",
            expected_project_hash=expected_project_hash,
            expected_session_id=cls._required_text(
                expected_session_scope, "session_id", "fresh commit session"
            ),
            expected_pid=int(expected_session_scope["pid"]),
            expected_hwnd=int(expected_session_scope["hwnd"]),
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


def validated_terminal_ld_reopen(transaction: ChangeTransaction) -> dict[str, Any] | None:
    """Return the attached exact fresh LD session for a completed operand edit."""

    if transaction.state is not ChangeTransactionState.COMPLETED:
        return None
    if transaction.plan.get("verification_scope") == CHECK_ONLY_VERIFICATION_SCOPE:
        return None
    verification = transaction.result.get("verification")
    if not isinstance(verification, dict):
        return None
    receipt = verification.get("fresh_reopen")
    attached_result = verification.get("fresh_reopen_attach")
    attached = (
        attached_result.get("session")
        if isinstance(attached_result, dict) and isinstance(attached_result.get("session"), dict)
        else attached_result
    )
    if not isinstance(receipt, dict) or not isinstance(attached, dict):
        return None
    fresh = receipt.get("fresh_reopen")
    approval = receipt.get("approval_binding")
    readback = receipt.get("program_readback")
    if not isinstance(fresh, dict) or not isinstance(approval, dict) or not isinstance(readback, dict):
        raise ChangeExecutionPreconditionError("terminal LD reopen evidence is incomplete")
    target = transaction.target
    expected_program_id = transaction.plan.get("target", {}).get(
        "program_id", transaction.plan.get("target", {}).get("programId")
    )
    if (
        receipt.get("ok") is not True
        or receipt.get("verified") is not True
        or receipt.get("operation") != "project.verify_ld_operand_reopen"
        or fresh.get("source_pid") != target.engineering_process_id
        or fresh.get("source_hwnd") != target.engineering_window_handle
        or fresh.get("source_closed") is not True
        or not isinstance(fresh.get("pid"), int)
        or not isinstance(fresh.get("hwnd"), int)
        or fresh["pid"] <= 0
        or fresh["hwnd"] <= 0
        or fresh["pid"] == target.engineering_process_id
        or fresh["hwnd"] == target.engineering_window_handle
        or fresh.get("workspace_id") != target.workspace_id
        or fresh.get("project_path") != target.project_path
        or not isinstance(fresh.get("project_sha256"), str)
        or len(fresh["project_sha256"]) != 64
        or approval.get("approval_id") != transaction.id
        or approval.get("approval_plan_digest") != transaction.plan_digest
        or readback.get("program_id") != expected_program_id
        or readback.get("final_view") != "LD"
        or readback.get("stable_repeat") is not True
        or attached.get("pid") != fresh["pid"]
        or attached.get("hwnd") != fresh["hwnd"]
        or attached.get("project_path") != fresh["project_path"]
        or attached.get("project_hash") != fresh["project_sha256"]
        or not isinstance(attached.get("session_id"), str)
        or not attached["session_id"]
    ):
        raise ChangeExecutionPreconditionError(
            "terminal LD reopen evidence differs from the approved change"
        )
    return dict(attached)


def _nonempty_receipt(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise ChangeExecutionPreconditionError(f"{label} receipt is missing")
    return value


def _same_project_path(left: object, right: object) -> bool:
    if not isinstance(left, str) or not left or not isinstance(right, str) or not right:
        return False
    return os.path.normcase(str(Path(left).expanduser().resolve())) == os.path.normcase(
        str(Path(right).expanduser().resolve())
    )


def _validate_terminal_plan_and_boundary(
    transaction: ChangeTransaction,
    evidence: dict[str, Any],
) -> str:
    if transaction.plan.get("plan_kind") is not None:
        raise ChangeExecutionPreconditionError("terminal LD validator received a structural plan")
    expected_plan_digest = calculate_plan_digest(
        target=transaction.target,
        risk_level=transaction.risk_level,
        intent=transaction.intent,
        plan=transaction.plan,
    )
    approval = transaction.approval
    if (
        transaction.plan_digest != expected_plan_digest
        or approval is None
        or approval.decision.value != "APPROVE"
        or approval.plan_digest != transaction.plan_digest
    ):
        raise ChangeExecutionPreconditionError(
            "terminal LD receipt is not bound to the canonical approved plan"
        )

    expected = MCPChangeExecutionService._verification_contract_evidence(transaction)
    for field_name in (
        "verification_scope",
        "executed_checks",
        "not_executed",
        "manual_follow_up",
    ):
        if evidence.get(field_name) != expected[field_name]:
            raise ChangeExecutionPreconditionError(
                f"terminal LD receipt mismatched {field_name}"
            )
    return str(expected["verification_scope"])


def _checkpoint_plan_binding(
    checkpoint: dict[str, Any],
    *,
    mcp_plan_id: str,
    mcp_plan_digest: str,
    label: str,
) -> None:
    if (
        checkpoint.get("plan_id") != mcp_plan_id
        or checkpoint.get("plan_digest") != mcp_plan_digest
    ):
        raise ChangeExecutionPreconditionError(
            f"{label} receipt changed the immutable native plan"
        )


def _validate_exact_ld_diff(
    value: object,
    *,
    label: str,
    program_name: str,
    token_offset: int,
    expected_operand: str,
    replacement_operand: str,
    expected_payload_sha256_before: str | None,
) -> dict[str, str]:
    """Validate the complete public token-diff contract without raw LD bytes."""

    diff = _nonempty_receipt(value, label)
    payload_before = MCPChangeExecutionService._required_sha256(
        diff, "payload_sha256_before", label
    )
    payload_after = MCPChangeExecutionService._required_sha256(
        diff, "payload_sha256_after", label
    )
    size_before = diff.get("payload_size_before")
    size_after = diff.get("payload_size_after")
    if (
        set(diff)
        != {
            "program",
            "payload_sha256_before",
            "payload_sha256_after",
            "payload_size_before",
            "payload_size_after",
            "byte_length_unchanged",
            "change_count",
            "changes",
        }
        or diff.get("program") != program_name
        or (
            expected_payload_sha256_before is not None
            and payload_before != expected_payload_sha256_before
        )
        or isinstance(size_before, bool)
        or not isinstance(size_before, int)
        or size_before <= 0
        or isinstance(size_after, bool)
        or not isinstance(size_after, int)
        or size_after != size_before
        or diff.get("byte_length_unchanged") is not True
        or diff.get("change_count") != 1
        or diff.get("changes")
        != [
            {
                "token_offset": token_offset,
                "before": expected_operand,
                "after": replacement_operand,
            }
        ]
    ):
        raise ChangeExecutionPreconditionError(
            f"{label} differs from the exact approved operand change"
        )
    return {
        "payload_sha256_before": payload_before,
        "payload_sha256_after": payload_after,
    }


def _validate_terminal_apply_envelope(
    transaction: ChangeTransaction,
    value: object,
) -> dict[str, Any]:
    envelope = _nonempty_receipt(value, "apply")
    service = MCPChangeExecutionService
    target = transaction.target
    project_identity_digest = service._project_identity_digest(target)
    mcp_transaction_id = service._required_text(
        envelope, "mcp_transaction_id", "apply evidence"
    )
    mcp_plan_id = service._required_text(envelope, "mcp_plan_id", "apply evidence")
    mcp_plan_digest = service._required_sha256(
        envelope, "mcp_plan_digest", "apply evidence"
    )
    if (
        envelope.get("host_transaction_id") != transaction.id
        or envelope.get("host_plan_digest") != transaction.plan_digest
        or envelope.get("project_identity_digest") != project_identity_digest
    ):
        raise ChangeExecutionPreconditionError(
            "apply evidence differs from the Host transaction binding"
        )

    snapshot = _nonempty_receipt(envelope.get("snapshot"), "project.snapshot")
    backup = _nonempty_receipt(envelope.get("backup"), "project.backup")
    mcp_plan = _nonempty_receipt(envelope.get("mcp_plan"), "program.plan_change")
    applied = _nonempty_receipt(envelope.get("apply"), "program.apply_ld_patch")
    snapshot_id = service._required_text(snapshot, "snapshot_id", "project.snapshot")
    backup_id = service._required_text(backup, "backup_id", "project.backup")
    source_scope = {
        "session_id": target.engineering_session_id,
        "pid": target.engineering_process_id,
        "hwnd": target.engineering_window_handle,
        "project_path": target.project_path,
        "project_hash": target.artifact_hash,
    }

    for receipt, operation, expected_state in (
        (snapshot, "project.snapshot", "SNAPSHOT_CREATED"),
        (backup, "project.backup", "BACKUP_CREATED"),
    ):
        checkpoint = service._validate_verified_operation(
            result=receipt,
            operation=operation,
            transaction=transaction,
            mcp_transaction_id=mcp_transaction_id,
            expected_state=expected_state,
            expected_project_hash=target.artifact_hash,
        )
        if receipt.get("session_id") != target.engineering_session_id:
            raise ChangeExecutionPreconditionError(f"{operation} mismatched session_id")
        if expected_state == "SNAPSHOT_CREATED":
            if (
                receipt.get("snapshot_id") != snapshot_id
                or receipt.get("project_hash") != target.artifact_hash
                or not _same_project_path(receipt.get("project_path"), target.project_path)
                or receipt.get("project_unchanged") is not True
            ):
                raise ChangeExecutionPreconditionError(
                    "project.snapshot differs from the approved target"
                )
        else:
            if (
                receipt.get("backup_id") != backup_id
                or receipt.get("transaction_id") != mcp_transaction_id
                or receipt.get("project_hash") != target.artifact_hash
                or receipt.get("backup_hash") != target.artifact_hash
                or not _same_project_path(receipt.get("project_path"), target.project_path)
            ):
                raise ChangeExecutionPreconditionError(
                    "project.backup differs from the approved target"
                )
        if checkpoint.get("host_transaction_id") != transaction.id:
            raise ChangeExecutionPreconditionError(
                f"{operation} checkpoint mismatched the Host transaction"
            )

    plan_arguments = {
        **source_scope,
        "transaction_id": mcp_transaction_id,
        "snapshot_id": snapshot_id,
        "backup_id": backup_id,
        **service._native_plan_arguments(transaction),
    }
    service._validate_mcp_plan_contract(
        transaction=transaction,
        plan_contract=mcp_plan,
        plan_arguments=plan_arguments,
        snapshot=snapshot,
    )
    if (
        mcp_plan.get("plan_id") != mcp_plan_id
        or mcp_plan.get("plan_digest") != mcp_plan_digest
        or service._mcp_plan_digest(mcp_plan) != mcp_plan_digest
    ):
        raise ChangeExecutionPreconditionError(
            "apply evidence contains a mismatched native plan digest"
        )

    applied_checkpoint = service._validate_verified_operation(
        result=applied,
        operation="program.apply_ld_patch",
        transaction=transaction,
        mcp_transaction_id=mcp_transaction_id,
        expected_state="APPLIED",
        expected_project_hash=target.artifact_hash,
    )
    _checkpoint_plan_binding(
        applied_checkpoint,
        mcp_plan_id=mcp_plan_id,
        mcp_plan_digest=mcp_plan_digest,
        label="program.apply_ld_patch",
    )
    native_replacement = service._required_text(
        mcp_plan,
        (
            "native_replacement"
            if mcp_plan.get("native_replacement") is not None
            else "replacement"
        ),
        "program.plan_change",
    )
    applied_diff = _validate_exact_ld_diff(
        applied.get("diff"),
        label="program.apply_ld_patch diff",
        program_name=service._required_text(
            mcp_plan, "program_name", "program.plan_change"
        ),
        token_offset=int(mcp_plan["token_offset"]),
        expected_operand=service._required_text(
            mcp_plan, "expected", "program.plan_change"
        ),
        replacement_operand=native_replacement,
        expected_payload_sha256_before=service._required_sha256(
            mcp_plan, "payload_sha256_before", "program.plan_change"
        ),
    )
    applied_payload_after = service._required_sha256(
        applied, "payload_sha256_after", "program.apply_ld_patch"
    )
    planned_canonical_after = service._required_sha256(
        mcp_plan,
        "payload_canonical_sha256_planned",
        "program.plan_change",
    )
    applied_canonical_after = service._required_sha256(
        applied,
        "payload_canonical_sha256_after",
        "program.apply_ld_patch",
    )
    if (
        applied.get("session_id") != target.engineering_session_id
        or applied.get("plan_id") != mcp_plan_id
        or applied.get("matches_plan") is not True
        or applied.get("matches_native_plan") is not True
        or applied.get("project_unchanged") is not True
        or applied.get("payload_sha256_before") != mcp_plan.get("payload_sha256_before")
        or applied_payload_after != applied_diff["payload_sha256_after"]
        or applied_canonical_after != planned_canonical_after
    ):
        raise ChangeExecutionPreconditionError(
            "program.apply_ld_patch differs from the planned exact operand change"
        )
    return {
        "mcp_transaction_id": mcp_transaction_id,
        "mcp_plan_id": mcp_plan_id,
        "mcp_plan_digest": mcp_plan_digest,
        "mcp_plan": mcp_plan,
        "project_identity_digest": project_identity_digest,
        "source_scope": source_scope,
        "apply_receipt": applied,
        "program_name": mcp_plan["program_name"],
        "token_offset": mcp_plan["token_offset"],
        "expected_operand": mcp_plan["expected"],
        "replacement_operand": native_replacement,
        "payload_sha256_before": applied_diff["payload_sha256_before"],
        "payload_sha256_after": applied_payload_after,
        "payload_canonical_sha256_after": applied_canonical_after,
    }


def _validate_terminal_lookup_binding(
    transaction: ChangeTransaction,
    value: object,
) -> dict[str, Any]:
    lookup = _nonempty_receipt(value, "change.transaction_lookup")
    if (
        lookup.get("ok") is not True
        or lookup.get("verified") is not True
        or lookup.get("operation") != "change.transaction_lookup"
        or lookup.get("found") is not True
        or lookup.get("host_transaction_id") != transaction.id
    ):
        raise ChangeExecutionPreconditionError(
            "change.transaction_lookup did not prove the Host transaction"
        )
    checkpoint = MCPChangeExecutionService._recovery_checkpoint(
        lookup, "transaction_checkpoint", "change.transaction_lookup"
    )
    MCPChangeExecutionService._validate_recovery_checkpoint(
        transaction=transaction,
        checkpoint=checkpoint,
    )
    mcp_transaction_id = MCPChangeExecutionService._required_text(
        checkpoint, "transaction_id", "change.transaction_lookup checkpoint"
    )
    mcp_plan_id = MCPChangeExecutionService._required_text(
        checkpoint, "plan_id", "change.transaction_lookup checkpoint"
    )
    mcp_plan_digest = MCPChangeExecutionService._required_sha256(
        checkpoint, "plan_digest", "change.transaction_lookup checkpoint"
    )
    project_identity_digest = MCPChangeExecutionService._required_sha256(
        checkpoint,
        "project_identity_digest",
        "change.transaction_lookup checkpoint",
    )
    canonical_value = checkpoint.get("payload_canonical_sha256_planned")
    planned_canonical_after = (
        MCPChangeExecutionService._required_sha256(
            checkpoint,
            "payload_canonical_sha256_planned",
            "change.transaction_lookup checkpoint",
        )
        if canonical_value is not None
        else None
    )
    stored_checkpoint = MCPChangeExecutionService._stored_native_checkpoint(
        transaction
    )
    if stored_checkpoint is not None:
        stored_canonical_value = stored_checkpoint.get(
            "payload_canonical_sha256_planned"
        )
        stored_canonical_after = (
            MCPChangeExecutionService._normalize_sha256(
                stored_canonical_value,
                "stored native payload_canonical_sha256_planned",
            )
            if stored_canonical_value is not None
            else None
        )
        if planned_canonical_after != stored_canonical_after:
            raise ChangeExecutionPreconditionError(
                "change.transaction_lookup canonical plan binding differs from "
                "the Host checkpoint"
            )
    native_plan = MCPChangeExecutionService._native_plan_arguments(transaction)
    plan_target = transaction.plan.get("target")
    if not isinstance(plan_target, Mapping):
        raise ChangeExecutionPreconditionError(
            "terminal LD plan has no target program identity"
        )
    program_name = plan_target.get(
        "program_name", plan_target.get("programName")
    )
    if (
        not isinstance(program_name, str)
        or not program_name
        or checkpoint.get("program_name") != program_name
    ):
        raise ChangeExecutionPreconditionError(
            "terminal LD checkpoint has no exact target program name"
        )
    native_replacement_value = checkpoint.get("native_replacement")
    native_replacement = (
        MCPChangeExecutionService._required_text(
            checkpoint,
            "native_replacement",
            "change.transaction_lookup checkpoint",
        )
        if native_replacement_value is not None
        else None
    )
    if (native_replacement is None) != (planned_canonical_after is None):
        raise ChangeExecutionPreconditionError(
            "terminal LD checkpoint has an incomplete canonical plan binding"
        )
    if stored_checkpoint is not None:
        stored_native_replacement = stored_checkpoint.get("native_replacement")
        if (
            stored_native_replacement is not None
            and stored_native_replacement != native_replacement
        ):
            raise ChangeExecutionPreconditionError(
                "change.transaction_lookup native replacement differs from the Host checkpoint"
            )
    return {
        "mcp_transaction_id": mcp_transaction_id,
        "mcp_plan_id": mcp_plan_id,
        "mcp_plan_digest": mcp_plan_digest,
        "mcp_plan": None,
        "project_identity_digest": project_identity_digest,
        "source_scope": {
            "session_id": transaction.target.engineering_session_id,
            "pid": transaction.target.engineering_process_id,
            "hwnd": transaction.target.engineering_window_handle,
            "project_path": transaction.target.project_path,
            "project_hash": transaction.target.artifact_hash,
        },
        "apply_receipt": None,
        "program_name": program_name,
        "token_offset": native_plan["token_offset"],
        "expected_operand": native_plan["expected"],
        "replacement_operand": native_replacement,
        "payload_sha256_before": None,
        "payload_sha256_after": None,
        "payload_canonical_sha256_after": planned_canonical_after,
    }


def _validate_status_receipt(
    transaction: ChangeTransaction,
    value: object,
    *,
    binding: Mapping[str, Any],
    allowed_states: set[str],
    label: str,
) -> dict[str, Any]:
    status = _nonempty_receipt(value, label)
    if (
        status.get("ok") is not True
        or status.get("verified") is not True
        or status.get("operation") != "change.transaction_status"
    ):
        raise ChangeExecutionPreconditionError(f"{label} is not verified")
    native = _nonempty_receipt(status.get("transaction"), f"{label} transaction")
    checkpoint = MCPChangeExecutionService._recovery_checkpoint(
        status, "transaction_checkpoint", label
    )
    mcp_transaction_id = str(binding["mcp_transaction_id"])
    mcp_plan_id = str(binding["mcp_plan_id"])
    mcp_plan_digest = str(binding["mcp_plan_digest"])
    project_hash = MCPChangeExecutionService._required_sha256(
        native, "current_project_hash", label
    )
    session_id = MCPChangeExecutionService._required_text(
        native, "active_session_id", label
    )
    pid = native.get("active_pid")
    hwnd = native.get("active_hwnd")
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 0
        or isinstance(hwnd, bool)
        or not isinstance(hwnd, int)
        or hwnd <= 0
        or native.get("transaction_id") != mcp_transaction_id
        or native.get("state") not in allowed_states
        or checkpoint.get("state") != native.get("state")
        or not _same_project_path(native.get("project_path"), transaction.target.project_path)
    ):
        raise ChangeExecutionPreconditionError(f"{label} target identity is invalid")
    MCPChangeExecutionService._validate_recovery_checkpoint(
        transaction=transaction,
        checkpoint=checkpoint,
        expected_transaction_id=mcp_transaction_id,
        expected_session_id=session_id,
        expected_pid=pid,
        expected_hwnd=hwnd,
        expected_project_hash=project_hash,
    )
    _checkpoint_plan_binding(
        checkpoint,
        mcp_plan_id=mcp_plan_id,
        mcp_plan_digest=mcp_plan_digest,
        label=label,
    )
    return {
        "session_id": session_id,
        "pid": pid,
        "hwnd": hwnd,
        "project_path": transaction.target.project_path,
        "project_hash": project_hash,
        "state": native["state"],
        "checkpoint": checkpoint,
    }


def _validate_bound_operation(
    transaction: ChangeTransaction,
    value: object,
    *,
    binding: Mapping[str, Any],
    scope: Mapping[str, Any],
    operation: str,
    expected_state: str,
    expected_project_hash: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt = _nonempty_receipt(value, operation)
    checkpoint = MCPChangeExecutionService._validate_verified_operation(
        result=receipt,
        operation=operation,
        transaction=transaction,
        mcp_transaction_id=str(binding["mcp_transaction_id"]),
        expected_state=expected_state,
        expected_project_hash=expected_project_hash,
        expected_session_id=str(scope["session_id"]),
        expected_pid=int(scope["pid"]),
        expected_hwnd=int(scope["hwnd"]),
    )
    _checkpoint_plan_binding(
        checkpoint,
        mcp_plan_id=str(binding["mcp_plan_id"]),
        mcp_plan_digest=str(binding["mcp_plan_digest"]),
        label=operation,
    )
    return receipt, checkpoint


def _validate_save_receipt(
    transaction: ChangeTransaction,
    value: object,
    *,
    binding: Mapping[str, Any],
    scope: Mapping[str, Any],
    expected_state: str,
) -> tuple[dict[str, Any], str]:
    receipt = _nonempty_receipt(value, "project.commit_save")
    project_hash = MCPChangeExecutionService._required_sha256(
        receipt, "project_hash", "project.commit_save"
    )
    receipt, checkpoint = _validate_bound_operation(
        transaction,
        receipt,
        binding=binding,
        scope=scope,
        operation="project.commit_save",
        expected_state=expected_state,
        expected_project_hash=project_hash,
    )
    if (
        not _same_project_path(receipt.get("project_path"), transaction.target.project_path)
        or checkpoint.get("current_project_hash") != project_hash
    ):
        raise ChangeExecutionPreconditionError(
            "project.commit_save differs from the approved working copy"
        )
    if expected_state == "ROLLED_BACK_SAVED" and (
        checkpoint.get("terminal") is not True
        or checkpoint.get("recovery_action") != "already_terminal"
        or checkpoint.get("lease_status") != "released"
    ):
        raise ChangeExecutionPreconditionError(
            "rollback save is not a released terminal receipt"
        )
    return receipt, project_hash


def _validate_diff_receipt(
    transaction: ChangeTransaction,
    value: object,
    *,
    binding: Mapping[str, Any],
    scope: Mapping[str, Any],
    reconciliation: bool,
) -> dict[str, Any]:
    receipt = _nonempty_receipt(value, "project.diff")
    MCPChangeExecutionService._validate_diff_result(
        transaction=transaction,
        result=receipt,
        mcp_transaction_id=str(binding["mcp_transaction_id"]),
        mcp_plan_id=str(binding["mcp_plan_id"]),
        expected_session_scope=scope,
    )
    checkpoint = MCPChangeExecutionService._recovery_checkpoint(
        receipt, "transaction_checkpoint", "project.diff"
    )
    _checkpoint_plan_binding(
        checkpoint,
        mcp_plan_id=str(binding["mcp_plan_id"]),
        mcp_plan_digest=str(binding["mcp_plan_digest"]),
        label="project.diff",
    )
    requested_replacement = MCPChangeExecutionService._required_text(
        receipt, "requested_replacement", "project.diff"
    )
    native_replacement = MCPChangeExecutionService._required_text(
        receipt, "native_replacement", "project.diff"
    )
    native_plan = MCPChangeExecutionService._native_plan_arguments(transaction)
    expected_replacement = binding.get("replacement_operand")
    if (
        requested_replacement != native_plan["replacement"]
        or (
            expected_replacement is not None
            and native_replacement != expected_replacement
        )
        or receipt.get("native_contract_reconciliation") is not reconciliation
    ):
        raise ChangeExecutionPreconditionError(
            "project.diff changed the approved native operand contract"
        )
    diff = _validate_exact_ld_diff(
        receipt.get("diff"),
        label="project.diff canonical diff",
        program_name=str(binding["program_name"]),
        token_offset=int(binding["token_offset"]),
        expected_operand=str(binding["expected_operand"]),
        replacement_operand=native_replacement,
        expected_payload_sha256_before=(
            str(binding["payload_sha256_before"])
            if binding.get("payload_sha256_before") is not None
            else None
        ),
    )
    canonical_value = receipt.get("payload_canonical_sha256_after")
    canonical_after = (
        MCPChangeExecutionService._required_sha256(
            receipt, "payload_canonical_sha256_after", "project.diff"
        )
        if canonical_value is not None
        else None
    )
    expected_canonical = binding.get("payload_canonical_sha256_after")
    if (canonical_after is None) != (expected_canonical is None) or (
        canonical_after is not None and canonical_after != expected_canonical
    ):
        raise ChangeExecutionPreconditionError(
            "project.diff canonical payload differs from the apply receipt"
        )
    return {
        **diff,
        "receipt": receipt,
        "replacement_operand": native_replacement,
        "payload_canonical_sha256_after": canonical_after,
        "canonical_receipt_present": canonical_value is not None,
    }


def _validate_validation_receipt(
    transaction: ChangeTransaction,
    value: object,
    *,
    binding: Mapping[str, Any],
    scope: Mapping[str, Any],
    expected_diff: Mapping[str, Any],
) -> dict[str, Any]:
    receipt = _nonempty_receipt(value, "project.validate_change")
    MCPChangeExecutionService._validate_validation_result(
        transaction=transaction,
        result=receipt,
        mcp_transaction_id=str(binding["mcp_transaction_id"]),
        mcp_plan_id=str(binding["mcp_plan_id"]),
        expected_session_scope=scope,
    )
    checkpoint = MCPChangeExecutionService._recovery_checkpoint(
        receipt, "transaction_checkpoint", "project.validate_change"
    )
    _checkpoint_plan_binding(
        checkpoint,
        mcp_plan_id=str(binding["mcp_plan_id"]),
        mcp_plan_digest=str(binding["mcp_plan_digest"]),
        label="project.validate_change",
    )
    _nonempty_receipt(receipt.get("program_check"), "XG5000 Program Check")
    _validate_exact_ld_diff(
        receipt.get("readback_diff"),
        label="validation readback diff",
        program_name=str(binding["program_name"]),
        token_offset=int(binding["token_offset"]),
        expected_operand=str(binding["expected_operand"]),
        replacement_operand=str(expected_diff["replacement_operand"]),
        expected_payload_sha256_before=str(expected_diff["payload_sha256_before"]),
    )
    canonical_value = receipt.get("payload_canonical_sha256_after")
    canonical_after = (
        MCPChangeExecutionService._required_sha256(
            receipt,
            "payload_canonical_sha256_after",
            "project.validate_change",
        )
        if canonical_value is not None
        else None
    )
    if (
        (canonical_value is not None)
        != bool(expected_diff["canonical_receipt_present"])
        or (
            canonical_after is not None
            and canonical_after
            != expected_diff["payload_canonical_sha256_after"]
        )
    ):
        raise ChangeExecutionPreconditionError(
            "validation canonical payload differs from project.diff"
        )
    return receipt


def _validate_completed_ld_receipt(
    transaction: ChangeTransaction,
    evidence: dict[str, Any],
    *,
    binding: Mapping[str, Any],
    reconciliation: bool,
    verification_scope: str,
) -> None:
    source_scope = dict(binding["source_scope"])
    if reconciliation:
        source_scope = _validate_status_receipt(
            transaction,
            evidence.get("status"),
            binding=binding,
            allowed_states={"QUARANTINED"},
            label="reconciliation status",
        )
    diff_key = "native_contract_reconciliation" if reconciliation else "diff"
    diff = _validate_diff_receipt(
        transaction,
        evidence.get(diff_key),
        binding=binding,
        scope=source_scope,
        reconciliation=reconciliation,
    )
    _validate_validation_receipt(
        transaction,
        evidence.get("validation"),
        binding=binding,
        scope=source_scope,
        expected_diff=diff,
    )
    _save, saved_hash = _validate_save_receipt(
        transaction,
        evidence.get("save"),
        binding=binding,
        scope=source_scope,
        expected_state="SAVED",
    )
    saved_scope = _validate_status_receipt(
        transaction,
        evidence.get("saved_transaction_status"),
        binding=binding,
        allowed_states={"SAVED"},
        label="saved transaction status",
    )
    if (
        saved_scope["project_hash"] != saved_hash
        or saved_scope["session_id"] != source_scope["session_id"]
        or saved_scope["pid"] != source_scope["pid"]
        or saved_scope["hwnd"] != source_scope["hwnd"]
    ):
        raise ChangeExecutionPreconditionError(
            "saved transaction status differs from the save receipt"
        )

    if verification_scope == CHECK_ONLY_VERIFICATION_SCOPE:
        if "fresh_reopen" in evidence or "fresh_reopen_attach" in evidence:
            raise ChangeExecutionPreconditionError(
                "check-only terminal receipt unexpectedly contains reopen evidence"
            )
        commit_scope = saved_scope
    else:
        reopen = _nonempty_receipt(
            evidence.get("fresh_reopen"), "project.verify_ld_operand_reopen"
        )
        attached = _nonempty_receipt(
            evidence.get("fresh_reopen_attach"), "fresh reopen attach"
        )
        mcp_plan = binding.get("mcp_plan")
        expected_payload_hash = (
            MCPChangeExecutionService._required_sha256(
                mcp_plan, "payload_sha256_planned", "apply MCP plan"
            )
            if isinstance(mcp_plan, dict)
            else str(diff["payload_sha256_after"])
        )
        digest_input: object = (
            {"native_contract_reconciliation": diff["receipt"]}
            if reconciliation
            else binding.get("apply_receipt")
        )
        result_digest = MCPChangeExecutionService._ld_fresh_reopen_result_digest(
            transaction=transaction,
            mcp_transaction_id=str(binding["mcp_transaction_id"]),
            mcp_plan_id=str(binding["mcp_plan_id"]),
            mcp_plan_digest=str(binding["mcp_plan_digest"]),
            saved_project_hash=saved_hash,
            apply_evidence=digest_input,
        )
        fresh_scope = MCPChangeExecutionService._validate_ld_fresh_reopen_result(
            transaction=transaction,
            result=reopen,
            mcp_transaction_id=str(binding["mcp_transaction_id"]),
            mcp_plan_id=str(binding["mcp_plan_id"]),
            mcp_plan_digest=str(binding["mcp_plan_digest"]),
            saved_project_hash=saved_hash,
            result_digest=result_digest,
            expected_ld_payload_sha256=expected_payload_hash,
        )
        readback = _nonempty_receipt(
            reopen.get("program_readback"), "fresh reopen program readback"
        )
        canonical_after = diff.get("payload_canonical_sha256_after")
        if canonical_after is not None and (
            MCPChangeExecutionService._required_sha256(
                readback,
                "ld_canonical_sha256",
                "fresh reopen program readback",
            )
            != canonical_after
        ):
            raise ChangeExecutionPreconditionError(
                "fresh reopen canonical payload differs from validation"
            )
        reopen_checkpoint = MCPChangeExecutionService._recovery_checkpoint(
            reopen,
            "transaction_checkpoint",
            "project.verify_ld_operand_reopen",
        )
        MCPChangeExecutionService._validate_recovery_checkpoint(
            transaction=transaction,
            checkpoint=reopen_checkpoint,
            expected_transaction_id=str(binding["mcp_transaction_id"]),
            expected_session_id=str(saved_scope["session_id"]),
            expected_pid=int(saved_scope["pid"]),
            expected_hwnd=int(saved_scope["hwnd"]),
            expected_project_hash=saved_hash,
        )
        _checkpoint_plan_binding(
            reopen_checkpoint,
            mcp_plan_id=str(binding["mcp_plan_id"]),
            mcp_plan_digest=str(binding["mcp_plan_digest"]),
            label="project.verify_ld_operand_reopen",
        )
        if reopen_checkpoint.get("state") != "SAVED":
            raise ChangeExecutionPreconditionError(
                "fresh reopen receipt has the wrong native state"
            )
        commit_scope = MCPChangeExecutionService._validate_ld_fresh_reopen_attach(
            transaction=transaction,
            attached=attached,
            fresh_scope=fresh_scope,
        )

    commit = _nonempty_receipt(evidence.get("commit"), "change.commit")
    MCPChangeExecutionService._validate_commit_result(
        transaction=transaction,
        result=commit,
        mcp_transaction_id=str(binding["mcp_transaction_id"]),
        expected_project_hash=saved_hash,
        expected_session_scope=commit_scope,
    )
    commit_checkpoint = MCPChangeExecutionService._recovery_checkpoint(
        commit, "transaction_checkpoint", "change.commit"
    )
    _checkpoint_plan_binding(
        commit_checkpoint,
        mcp_plan_id=str(binding["mcp_plan_id"]),
        mcp_plan_digest=str(binding["mcp_plan_digest"]),
        label="change.commit",
    )


def _validate_rolled_back_ld_receipt(
    transaction: ChangeTransaction,
    evidence: dict[str, Any],
    *,
    binding: Mapping[str, Any],
    reconciliation: bool,
) -> None:
    prefix = [
        evidence.get("diff"),
        evidence.get("validation"),
        evidence.get("save"),
        evidence.get("saved_transaction_status"),
    ]
    seen_gap = False
    for item in prefix:
        if item is None:
            seen_gap = True
        elif seen_gap:
            raise ChangeExecutionPreconditionError(
                "rollback receipt contains a non-contiguous success prefix"
            )
    status_key = "status" if reconciliation else "recovery_transaction_status"
    recovery_scope = _validate_status_receipt(
        transaction,
        evidence.get(status_key),
        binding=binding,
        allowed_states={
            "APPLYING",
            "APPLIED",
            "DIFF_VERIFIED",
            "CHECKED",
            "SAVED",
            "QUARANTINED",
        },
        label="rollback source status",
    )
    execution_scope = (
        recovery_scope if reconciliation else dict(binding["source_scope"])
    )
    diff: dict[str, Any] | None = None
    if prefix[0] is not None:
        diff = _validate_diff_receipt(
            transaction,
            prefix[0],
            binding=binding,
            scope=execution_scope,
            reconciliation=reconciliation,
        )
    if prefix[1] is not None:
        if diff is None:
            raise ChangeExecutionPreconditionError(
                "validation receipt has no preceding exact diff"
            )
        _validate_validation_receipt(
            transaction,
            prefix[1],
            binding=binding,
            scope=execution_scope,
            expected_diff=diff,
        )
    if prefix[2] is not None:
        _save, saved_hash = _validate_save_receipt(
            transaction,
            prefix[2],
            binding=binding,
            scope=execution_scope,
            expected_state="SAVED",
        )
        if prefix[3] is not None:
            saved_scope = _validate_status_receipt(
                transaction,
                prefix[3],
                binding=binding,
                allowed_states={"SAVED"},
                label="saved transaction status",
            )
        else:
            saved_scope = recovery_scope
        if (
            saved_scope["project_hash"] != saved_hash
            or saved_scope["state"] not in {"SAVED", "QUARANTINED"}
        ):
            raise ChangeExecutionPreconditionError(
                "rollback prefix save and status hashes differ"
            )

    rollback, rollback_checkpoint = _validate_bound_operation(
        transaction,
        evidence.get("rollback"),
        binding=binding,
        scope=recovery_scope,
        operation="project.rollback_change",
        expected_state="ROLLED_BACK_EDITOR",
        expected_project_hash=str(recovery_scope["project_hash"]),
    )
    if (
        rollback.get("plan_id") != binding["mcp_plan_id"]
        or rollback.get("status") != "rolled_back"
        or rollback.get("matches_original_canonical") is not True
        or rollback.get("matches_original_logic_and_nonvolatile") is not True
        or rollback_checkpoint.get("recovery_action") != "rollback_required"
    ):
        raise ChangeExecutionPreconditionError(
            "project.rollback_change did not prove native restoration"
        )
    _rollback_save, rollback_hash = _validate_save_receipt(
        transaction,
        evidence.get("rollback_save"),
        binding=binding,
        scope=recovery_scope,
        expected_state="ROLLED_BACK_SAVED",
    )

    if reconciliation:
        attached = _nonempty_receipt(evidence.get("recovery_attach"), "recovery attach")
        attached_session = (
            attached.get("session")
            if isinstance(attached.get("session"), dict)
            else attached
        )
        if (
            not isinstance(attached_session, dict)
            or attached_session.get("session_id") != recovery_scope["session_id"]
            or attached_session.get("pid") != recovery_scope["pid"]
            or attached_session.get("hwnd") != recovery_scope["hwnd"]
            or attached_session.get("project_hash") != recovery_scope["project_hash"]
            or not _same_project_path(
                attached_session.get("project_path"), transaction.target.project_path
            )
        ):
            raise ChangeExecutionPreconditionError(
                "recovery attach differs from the rollback source status"
            )
        final_scope = _validate_status_receipt(
            transaction,
            evidence.get("final_status"),
            binding=binding,
            allowed_states={"ROLLED_BACK_SAVED"},
            label="terminal rollback status",
        )
        final_checkpoint = final_scope["checkpoint"]
        if (
            final_scope["session_id"] != recovery_scope["session_id"]
            or final_scope["pid"] != recovery_scope["pid"]
            or final_scope["hwnd"] != recovery_scope["hwnd"]
            or final_scope["project_hash"] != rollback_hash
            or final_checkpoint.get("terminal") is not True
            or final_checkpoint.get("recovery_action") != "already_terminal"
            or final_checkpoint.get("lease_status") != "released"
        ):
            raise ChangeExecutionPreconditionError(
                "terminal rollback status differs from the rollback save"
            )


def has_valid_terminal_ld_receipt(transaction: ChangeTransaction) -> bool:
    """Validate a durable legacy operand terminal receipt fail-closed.

    This deliberately replays the same target, plan, journal-checkpoint, and
    operation invariants used while executing the change.  It accepts both the
    original execution result and a complete reconciliation result, but never a
    lookup-only terminal claim or an empty operation mapping.
    """

    if transaction.state not in {
        ChangeTransactionState.COMPLETED,
        ChangeTransactionState.ROLLED_BACK,
    }:
        return False
    try:
        reconciliation_value = transaction.result.get("reconciliation")
        reconciliation = isinstance(reconciliation_value, dict)
        evidence = _nonempty_receipt(
            reconciliation_value
            if reconciliation
            else transaction.result.get("verification"),
            "terminal LD verification",
        )
        verification_scope = _validate_terminal_plan_and_boundary(
            transaction, evidence
        )
        apply_value = transaction.result.get("apply")
        lookup_binding = (
            _validate_terminal_lookup_binding(transaction, evidence.get("lookup"))
            if reconciliation
            else None
        )
        if isinstance(apply_value, dict) and apply_value:
            binding = _validate_terminal_apply_envelope(transaction, apply_value)
        elif reconciliation:
            assert lookup_binding is not None
            binding = lookup_binding
        else:
            raise ChangeExecutionPreconditionError(
                "terminal LD receipt has no canonical apply evidence"
            )
        if lookup_binding is not None and any(
            lookup_binding[field_name] != binding[field_name]
            for field_name in (
                "mcp_transaction_id",
                "mcp_plan_id",
                "mcp_plan_digest",
                "project_identity_digest",
                "program_name",
                "token_offset",
                "expected_operand",
                "replacement_operand",
                "payload_canonical_sha256_after",
            )
        ):
            raise ChangeExecutionPreconditionError(
                "reconciliation lookup differs from the apply transaction"
            )

        if transaction.state is ChangeTransactionState.COMPLETED:
            _validate_completed_ld_receipt(
                transaction,
                evidence,
                binding=binding,
                reconciliation=reconciliation,
                verification_scope=verification_scope,
            )
        else:
            _validate_rolled_back_ld_receipt(
                transaction,
                evidence,
                binding=binding,
                reconciliation=reconciliation,
            )
    except (
        ChangeExecutionPreconditionError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ):
        return False
    return True
