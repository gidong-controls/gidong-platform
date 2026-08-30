#!/usr/bin/env python3
"""Fail-closed CA-04--CA-07 disposable-workspace orchestrator.

The script deliberately has three separate phases:

* default/dry-run: validate and print the immutable execution contract;
* --prepare: create a *new* Host workspace from the reviewed disposable backup
  and ask Host to open only its working copy in XG5000;
* --approve-and-execute: consume an already-created LD operand transaction and
  then invoke the independently versioned fresh-reopen LD verifier.

It never constructs a change plan or MCP authorization.  Those are Host/MCP
authority objects and must originate from the Agent proposal and Host approval
flow.  Consequently this script cannot accidentally mutate the active afbf or
the historical d160 workspace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, build_opener

SHA256 = re.compile(r"^[0-9a-f]{64}$")
LIVE_RECEIPT_SCHEMA = "controlstudio-plc-live-gate-receipt/v1"
BINDING_SCHEMA = "controlstudio-ca04-ca07-proposal-binding/v1"
BACKUP_SHA256 = "e0eab4e1754e1bec95431d979b2e168060438d6ab32fd8c2e3b7153a77681db7"
FORBIDDEN_WORKSPACE_IDS = frozenset({
    "afbf96ab-dca8-4a56-b7a5-894520da1b17",
    "d160dcac-643d-4087-b5aa-f119695e446f",
})
PROGRAM_ID = "ld:55e388e7aea7a8ce"
PROGRAM_NAME = "MainProgram"
LD_VALUE_TOKEN_OFFSET = 18


class OrchestrationError(RuntimeError):
    """The requested live transition is not safe to start or certify."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _require_sha256(value: str, name: str) -> str:
    normalized = value.strip().casefold()
    if SHA256.fullmatch(normalized) is None:
        raise OrchestrationError(f"{name} must be a SHA-256 digest")
    return normalized


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_backup() -> Path:
    return (
        _repository_root()
        / "artifacts"
        / "mne-capability-certification-disposable-20260818"
        / "backup-UI 레더 수직검증 20260817.xgwx"
    )


def execution_contract(backup: Path, artifact_dir: Path) -> dict[str, Any]:
    """Return a serializable, non-secret description of the exact live path."""
    return {
        "schema": "controlstudio-ca04-ca07-one-shot-plan/v1",
        "gate": "CA-04-CA-07",
        "source": {
            "path": str(backup.resolve()),
            "sha256": BACKUP_SHA256,
            "confirmed_disposable": True,
        },
        "forbidden_workspace_ids": sorted(FORBIDDEN_WORKSPACE_IDS),
        "proposal": {
            "program_id": PROGRAM_ID,
            "program_name": PROGRAM_NAME,
            "language": "LD",
            "token_offset": LD_VALUE_TOKEN_OFFSET,
            "expected_operand": "P0020",
            "replacement_operand": "P0021",
            "scope": "offline working copy only; no PLC connection/download/force/write",
        },
        "host_calls": [
            {"method": "POST", "path": "/api/v1/workspaces/open-local"},
            {"method": "POST", "path": "/api/v1/change-transactions/{id}/decision"},
            {"method": "POST", "path": "/api/v1/change-transactions/{id}/execute"},
            {"method": "GET", "path": "/api/v1/change-transactions/{id}"},
            {"command": "fresh-reopen-ld-approval-readback", "authority": "independent CLI"},
        ],
        "receipt_path": str((artifact_dir / "ca04-ca07.live-receipt.json").resolve()),
    }


class HostClient:
    def __init__(self, base_url: str, launch_token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.launch_token = launch_token
        self.opener = build_opener()
        self.session_token = ""
        self.csrf_token = ""

    def _request(
        self, path: str, *, method: str = "GET", body: dict[str, Any] | None = None,
        idempotency_key: str | None = None, bearer: str | None = None,
    ) -> Any:
        headers: dict[str, str] = {"Accept": "application/json"}
        token = bearer if bearer is not None else self.session_token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if self.csrf_token and method != "GET":
            headers["X-CSRF-Token"] = self.csrf_token
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        payload = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body).encode("utf-8")
        request = Request(self.base_url + path, method=method, headers=headers, data=payload)
        try:
            with self.opener.open(request, timeout=45) as response:
                value = json.loads(response.read().decode("utf-8"))
        except (HTTPError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OrchestrationError(f"Host request {method} {path} failed: {exc}") from exc
        return value

    def authenticate(self) -> None:
        handoff = self._request("/api/launcher/handoff", method="POST", bearer=self.launch_token)
        if not isinstance(handoff, dict):
            raise OrchestrationError("launcher handoff returned a non-object")
        ticket = handoff.get("handoffTicket")
        if not isinstance(ticket, str) or not ticket:
            raise OrchestrationError("launcher handoff did not provide a ticket")
        session = self._request("/api/session", method="POST", bearer=ticket)
        if not isinstance(session, dict):
            raise OrchestrationError("Host session creation returned a non-object")
        token = session.get("sessionToken")
        if not isinstance(token, str) or not token:
            raise OrchestrationError("Host session creation did not provide a session token")
        self.session_token = token
        bootstrap = self._request("/api/v1/bootstrap")
        if not isinstance(bootstrap, dict):
            raise OrchestrationError("Host bootstrap returned a non-object")
        csrf = bootstrap.get("csrfToken")
        if not isinstance(csrf, str) or not csrf:
            raise OrchestrationError("Host bootstrap did not provide a CSRF token")
        self.csrf_token = csrf

    def prepare_workspace(self, backup: Path) -> dict[str, Any]:
        result = self._request(
            "/api/v1/workspaces/open-local",
            method="POST",
            body={
                "sourcePath": str(backup.resolve()),
                "expectedSourceHash": BACKUP_SHA256,
                "openInXg5000": True,
                "timeoutSeconds": 120,
            },
            idempotency_key=f"ca04-ca07-prepare-{uuid.uuid4()}",
        )
        if not isinstance(result, dict):
            raise OrchestrationError("workspace open returned a non-object")
        workspace = result.get("workspace")
        if not isinstance(workspace, dict):
            raise OrchestrationError("workspace open did not return a workspace")
        workspace_id = workspace.get("workspaceId")
        revision_id = workspace.get("revisionId")
        working = workspace.get("workingCopy")
        source = workspace.get("source")
        if (
            not isinstance(workspace_id, str)
            or not isinstance(revision_id, str)
            or workspace_id in FORBIDDEN_WORKSPACE_IDS
        ):
            raise OrchestrationError("refusing non-fresh or forbidden workspace id")
        if not isinstance(working, dict) or not isinstance(source, dict):
            raise OrchestrationError("workspace open did not retain source/working identity")
        if source.get("sha256") != BACKUP_SHA256 or working.get("baselineSha256") != BACKUP_SHA256:
            raise OrchestrationError("new workspace is not bound to the reviewed disposable backup")
        working_path = working.get("path")
        working_hash = working.get("currentSha256")
        launch = _object(result.get("launch"), "XG5000 launch evidence")
        session = _object(result.get("session"), "read-ready engineering session")
        instance = _object(launch.get("instance"), "launch selected instance")
        metadata = _object(session.get("metadata"), "engineering session metadata")
        if (
            launch.get("verified") is not True
            or launch.get("workspaceId") != workspace_id
            or launch.get("windowDetected") is not True
            or launch.get("projectIdentityVerifiedAgainstSelectedPid") is not True
            or launch.get("projectIdentityReadOnlyBoundToSelectedPid") is not True
            or launch.get("identityAssurance") != "strong_readback"
            or not _same_path(launch.get("workingPath"), working_path)
            or launch.get("workingHash") != working_hash
            or metadata.get("project_identity_verified_against_selected_pid") is not True
            or session.get("pid") != instance.get("pid")
            or session.get("hwnd") != instance.get("hwnd")
            or not _same_path(session.get("projectPath"), working_path)
            or session.get("projectHash") != working_hash
            or not isinstance(session.get("sessionId"), str)
            or not session.get("sessionId")
        ):
            raise OrchestrationError(
                "workspace open did not return one strong read-ready selected-PID XG5000 session"
            )
        return result

    def start_exact_proposal(self) -> dict[str, Any]:
        """Ask the Agent for the reviewed proposal and stop at Host approval.

        This intentionally asks for no approval or execution.  The resulting
        transaction is the only plan source accepted by the execution phase.
        """
        conversation = self._request(
            "/api/v1/conversations", method="POST",
            body={"title": "CA-04~07 disposable offline LD operand change", "workflow": "engineering"},
            idempotency_key=f"ca04-ca07-conversation-{uuid.uuid4()}",
        )
        if not isinstance(conversation, dict):
            raise OrchestrationError("proposal conversation returned a non-object")
        conversation_id = _field(conversation, "conversation_id")
        if not isinstance(conversation_id, str) or not conversation_id:
            raise OrchestrationError("proposal conversation was not created")
        prompt = (
            "Create the single offline operand proposal described by the Host-owned typed mission "
            "seed in trusted context. Derive and confirm its value-token offset from fresh LD evidence, "
            "then create exactly one Host transaction and stop at PENDING_APPROVAL. Do not approve, "
            "apply, save, simulate, connect to a PLC, download, force, or write controller I/O."
        )
        submitted = self._request(
            f"/api/v1/conversations/{conversation_id}/messages", method="POST",
            body={
                "operationId": f"ca04-ca07-proposal-{uuid.uuid4()}",
                "workflow": "engineering",
                "content": prompt,
                "changeRequestScope": "offline_candidate_revision",
                "verificationScope": "offline_native_reopen",
                "offlineOperandMission": exact_ld_operand_mission_seed(),
            },
            idempotency_key=f"ca04-ca07-proposal-message-{uuid.uuid4()}",
        )
        if not isinstance(submitted, dict):
            raise OrchestrationError("proposal submission returned a non-object")
        return {"conversation": conversation, "submission": submitted}

    def wait_for_pending_approval(
        self, conversation_id: str, expected_workspace_id: str, timeout_seconds: int
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            transactions = self._request(
                f"/api/v1/change-transactions?conversation_id={conversation_id}&limit=20"
            )
            if not isinstance(transactions, list):
                raise OrchestrationError("change transaction listing returned a non-list")
            pending = [
                item for item in transactions
                if isinstance(item, dict) and _field(item, "state") == "PENDING_APPROVAL"
            ]
            if len(pending) == 1:
                target = _object(_field(pending[0], "target_identity"), "pending target identity")
                if _field(target, "workspace_id") != expected_workspace_id:
                    raise OrchestrationError(
                        "pending proposal is not bound to the workspace opened from the disposable backup"
                    )
                _require_exact_ld_plan(_object(_field(pending[0], "plan"), "pending plan"))
                return pending[0]
            if len(pending) > 1:
                raise OrchestrationError("proposal created multiple pending transactions")
            time.sleep(1.0)
        raise OrchestrationError("timed out waiting for Agent proposal to reach PENDING_APPROVAL")

    def wait_for_approval_wait(
        self, conversation_id: str, run_id: str, binding: dict[str, Any], timeout_seconds: int
    ) -> None:
        """Avoid the PENDING_APPROVAL-before-Agent-terminal race without retries."""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            conversation = self._request(f"/api/v1/conversations/{conversation_id}")
            current = self._request(f"/api/v1/change-transactions/{binding['transaction_id']}")
            mission = _field(conversation, "engineering_mission") if isinstance(conversation, dict) else None
            if isinstance(mission, dict):
                snapshot = mission.get("snapshot")
                state = snapshot.get("status") if isinstance(snapshot, dict) else None
                if (
                    state == "waiting_for_approval"
                    and _field(mission, "change_transaction_id") == binding["transaction_id"]
                    and _field(mission, "updated_by_run_id") == run_id
                ):
                    _validate_execution_binding(binding, _object(current, "current transaction"), binding["transaction_id"], binding["plan_digest"])
                    return
                if state in {"blocked", "completed", "waiting_for_user"}:
                    raise OrchestrationError("proposal Agent reached a non-approval terminal mission state")
            time.sleep(1.0)
        raise OrchestrationError("timed out waiting for the proposal Agent to reach approval-wait state")

    def approve_and_execute(
        self, transaction_id: str, plan_digest: str, binding: dict[str, Any]
    ) -> dict[str, Any]:
        transaction_id = transaction_id.strip()
        if not transaction_id:
            raise OrchestrationError("transaction id is required")
        plan_digest = _require_sha256(plan_digest, "plan digest")
        current = self._request(f"/api/v1/change-transactions/{transaction_id}")
        if not isinstance(current, dict):
            raise OrchestrationError("change transaction read returned a non-object")
        _validate_execution_binding(binding, current, transaction_id, plan_digest)
        decision = self._request(
            f"/api/v1/change-transactions/{transaction_id}/decision",
            method="POST", body={"decision": "approve", "expectedPlanDigest": plan_digest},
            idempotency_key=f"ca04-ca07-decision-{transaction_id}",
        )
        if not isinstance(decision, dict):
            raise OrchestrationError("change decision returned a non-object")
        executed = self._request(
            f"/api/v1/change-transactions/{transaction_id}/execute",
            method="POST", body={"expectedPlanDigest": plan_digest},
            idempotency_key=f"ca04-ca07-execute-{transaction_id}",
        )
        if not isinstance(executed, dict):
            raise OrchestrationError("change execution start returned a non-object")
        return executed

    def wait_for_terminal(self, transaction_id: str, timeout_seconds: int) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            transaction = self._request(f"/api/v1/change-transactions/{transaction_id}")
            if not isinstance(transaction, dict):
                raise OrchestrationError("change transaction read returned a non-object")
            if _field(transaction, "state") in {"COMPLETED", "ROLLED_BACK", "FAILED", "QUARANTINED", "REJECTED", "EXPIRED"}:
                return transaction
            time.sleep(1.0)
        raise OrchestrationError("timed out waiting for the Host transaction terminal state")


def _load_launch_token(path: Path | None) -> str:
    if path is None:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise OrchestrationError("--launch-token-file or LOCALAPPDATA is required for live Host calls")
        path = Path(local_app_data) / "ControlStudio Development" / "session.token"
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise OrchestrationError(f"could not read launch token: {path}") from exc
    if not token:
        raise OrchestrationError("launch token is empty")
    return token


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json_object(path: Path, name: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrchestrationError(f"could not read {name}: {path}") from exc
    return _object(value, name)


def _same_path(left: object, right: object) -> bool:
    if not isinstance(left, str) or not left or not isinstance(right, str) or not right:
        return False
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _workspace_root_for(project_path: object, workspace_id: object) -> str:
    """Derive and verify the exact profile workspace root for one working copy."""
    if not isinstance(project_path, str) or not project_path or not isinstance(workspace_id, str):
        raise OrchestrationError("working copy has no project path/workspace identity")
    project = Path(project_path).resolve()
    workspace_dir = project.parent.parent
    root = workspace_dir.parent
    if (
        workspace_dir.name != workspace_id
        or not (workspace_dir / "workspace.json").is_file()
        or not project.is_file()
    ):
        raise OrchestrationError("working copy is not an exact profile workspace-root project path")
    return str(root)


def _field(value: dict[str, Any], snake: str) -> Any:
    """Read a Host JSON field independent of its pydantic alias setting."""
    camel = "".join(part.title() if index else part for index, part in enumerate(snake.split("_")))
    return value.get(snake, value.get(camel))


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OrchestrationError(f"terminal transaction has no valid {name}")
    return value


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def exact_ld_operand_mission_seed() -> dict[str, Any]:
    """Host-validated request seed; it intentionally omits a supplied offset."""
    return {
        "schemaVersion": "1",
        "programId": PROGRAM_ID,
        "programName": PROGRAM_NAME,
        "expectedOperand": "P0020",
        "replacementOperand": "P0021",
        "tokenOffsetSource": "fresh_ld_evidence",
    }


def _require_exact_ld_plan(plan: dict[str, Any]) -> None:
    """Reject a pending approval unless it is the reviewed one-token LD edit."""
    target = _object(plan.get("target"), "LD plan target")
    current = _object(plan.get("current"), "LD plan current value")
    proposed = _object(plan.get("proposed"), "LD plan proposed value")
    if (
        _field(target, "program_id") != PROGRAM_ID
        or _field(target, "token_offset") != LD_VALUE_TOKEN_OFFSET
        or current.get("value", current.get("operand")) != "P0020"
        or proposed.get("value", proposed.get("operand")) != "P0021"
    ):
        raise OrchestrationError(
            "pending approval is not the reviewed MainProgram P0020-to-P0021 LD operand plan"
        )


def _proposal_binding(
    prepared: dict[str, Any], pending: dict[str, Any], submission: dict[str, Any]
) -> dict[str, Any]:
    workspace = _object(prepared.get("workspace"), "prepared workspace")
    working = _object(workspace.get("workingCopy"), "prepared working copy")
    source = _object(workspace.get("source"), "prepared source")
    target = _object(_field(pending, "target_identity"), "pending target identity")
    transaction_id = _field(pending, "transaction_id")
    plan_digest = _field(pending, "plan_digest")
    conversation_id = _field(submission, "conversation_id")
    run_id = _field(submission, "run_id")
    for name, value in {
        "workspace id": _field(workspace, "workspace_id"),
        "revision id": _field(workspace, "revision_id"),
        "working path": _field(working, "path"),
        "transaction id": transaction_id,
        "conversation id": conversation_id,
        "run id": run_id,
    }.items():
        if not isinstance(value, str) or not value:
            raise OrchestrationError(f"proposal binding has no valid {name}")
    _require_sha256(str(_field(source, "sha256") or ""), "proposal source hash")
    _require_sha256(str(_field(working, "current_sha256") or ""), "proposal working hash")
    _require_sha256(str(plan_digest or ""), "proposal plan digest")
    _require_exact_ld_plan(_object(_field(pending, "plan"), "pending plan"))
    if (
        _field(target, "workspace_id") != _field(workspace, "workspace_id")
        or _field(target, "revision_id") != _field(workspace, "revision_id")
        or _field(target, "source_artifact_hash") != BACKUP_SHA256
        or _field(target, "artifact_hash") != _field(working, "current_sha256")
        or not _same_path(_field(target, "project_path"), _field(working, "path"))
    ):
        raise OrchestrationError("pending transaction does not exactly match the prepared disposable workspace")
    return {
        "schema": BINDING_SCHEMA,
        "source": {"path": _field(source, "path"), "sha256": BACKUP_SHA256},
        "workspace": {
            "workspace_id": _field(workspace, "workspace_id"),
            "revision_id": _field(workspace, "revision_id"),
            "workspace_root": _workspace_root_for(
                _field(working, "path"), _field(workspace, "workspace_id")
            ),
            "working_path": _field(working, "path"),
            "working_sha256": _field(working, "current_sha256"),
        },
        "mission": exact_ld_operand_mission_seed(),
        "transaction_id": transaction_id,
        "plan_digest": plan_digest,
        "conversation_id": conversation_id,
        "run_id": run_id,
    }


def _validate_execution_binding(
    binding: dict[str, Any], transaction: dict[str, Any], transaction_id: str, plan_digest: str
) -> None:
    if binding.get("schema") != BINDING_SCHEMA:
        raise OrchestrationError("proposal binding schema is invalid")
    if binding.get("transaction_id") != transaction_id or binding.get("plan_digest") != plan_digest:
        raise OrchestrationError("requested transaction does not match the persisted proposal binding")
    source = _object(binding.get("source"), "proposal binding source")
    workspace = _object(binding.get("workspace"), "proposal binding workspace")
    if source.get("sha256") != BACKUP_SHA256:
        raise OrchestrationError("proposal binding source is not the reviewed disposable backup")
    target = _object(_field(transaction, "target_identity"), "transaction target identity")
    if (
        _field(transaction, "transaction_id") != transaction_id
        or _field(transaction, "plan_digest") != plan_digest
        or _field(target, "source_artifact_hash") != BACKUP_SHA256
        or _field(target, "workspace_id") != workspace.get("workspace_id")
        or _field(target, "revision_id") != workspace.get("revision_id")
        or _field(target, "artifact_hash") != workspace.get("working_sha256")
        or not isinstance(workspace.get("workspace_root"), str)
        or not _same_path(_field(target, "project_path"), workspace.get("working_path"))
    ):
        raise OrchestrationError("transaction no longer exactly matches its persisted disposable proposal binding")
    _require_exact_ld_plan(_object(_field(transaction, "plan"), "transaction plan"))


def run_ld_fresh_reopen(
    cli: Path, transaction: dict[str, Any], binding: dict[str, Any], *, timeout_seconds: int
) -> dict[str, Any]:
    """Run the independent, receipt-bound LD fresh-reopen CLI exactly once."""
    if not cli.is_file():
        raise OrchestrationError(f"independent XG5000 CLI is unavailable: {cli}")
    target = _object(_field(transaction, "target_identity"), "target identity")
    bound_workspace = _object(binding.get("workspace"), "proposal binding workspace")
    result = _object(_field(transaction, "result"), "completed result")
    verification = _object(result.get("verification"), "verification evidence")
    saved = _object(verification.get("save"), "saved result")
    applied = _object(_object(result.get("apply"), "apply evidence").get("apply"), "LD apply receipt")
    workspace_id = _field(target, "workspace_id")
    source_pid, source_hwnd = _field(target, "engineering_process_id"), _field(target, "engineering_window_handle")
    project_path = _field(target, "project_path")
    project_hash = saved.get("project_hash")
    expected_ld = applied.get("payload_sha256_after")
    plan_digest = _require_sha256(str(_field(transaction, "plan_digest") or ""), "plan digest")
    transaction_id = str(_field(transaction, "transaction_id") or "").strip()
    if not transaction_id or not isinstance(workspace_id, str) or not workspace_id:
        raise OrchestrationError("completed transaction is missing approval/workspace identity")
    workspace_root = bound_workspace.get("workspace_root")
    if (
        not isinstance(workspace_root, str)
        or not workspace_root
        or not _same_path(project_path, bound_workspace.get("working_path"))
        or workspace_id != bound_workspace.get("workspace_id")
    ):
        raise OrchestrationError("fresh reopen has no exact persisted workspace-root binding")
    argv = [
        str(cli), "fresh-reopen-ld-approval-readback",
        "--source-pid", str(source_pid), "--source-hwnd", str(source_hwnd),
        "--workspace-id", workspace_id, "--workspace-root", workspace_root,
        "--project-path", str(project_path),
        "--project-hash", str(project_hash), "--program-name", PROGRAM_NAME,
        "--program-id", PROGRAM_ID, "--expected-ld-payload-sha256", str(expected_ld),
        "--approval-id", transaction_id, "--approval-plan-digest", plan_digest,
        "--result-digest", _canonical_digest(result), "--project-tree-query", PROGRAM_NAME,
        "--timeout", str(timeout_seconds),
    ]
    try:
        completed = subprocess.run(argv, check=False, capture_output=True, text=True, encoding="utf-8", timeout=timeout_seconds + 30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OrchestrationError(f"LD fresh-reopen CLI did not complete: {exc}") from exc
    try:
        record = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise OrchestrationError("LD fresh-reopen CLI returned non-JSON stdout") from exc
    if completed.returncode != 0 or not isinstance(record, dict) or record.get("ok") is not True:
        raise OrchestrationError("LD fresh-reopen CLI rejected its receipt-bound execution")
    value = record.get("result")
    if not isinstance(value, dict):
        raise OrchestrationError("LD fresh-reopen CLI returned no result object")
    return value


def build_live_receipt(
    transaction: dict[str, Any], fresh: dict[str, Any], manifest_sha256: str,
    binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map a completed LD operand transaction plus independent reopen evidence.

    Missing or non-terminal evidence raises rather than writing a plausible-looking
    PASS receipt.  This intentionally supports only the reviewed LD operand
    replacement path, not arbitrary Host transaction shapes.
    """
    if _field(transaction, "state") != "COMPLETED":
        raise OrchestrationError("CA receipt requires a completed Host transaction")
    transaction_id = _field(transaction, "transaction_id")
    plan_digest = _require_sha256(str(_field(transaction, "plan_digest") or ""), "transaction plan digest")
    target = _object(_field(transaction, "target_identity"), "target identity")
    result = _object(_field(transaction, "result"), "result")
    verification = _object(result.get("verification"), "verification evidence")
    saved = _object(verification.get("save"), "saved result")
    applied_outer = _object(result.get("apply"), "apply evidence")
    application = _object(applied_outer.get("apply"), "LD apply receipt")
    fresh_reopen = _object(fresh.get("fresh_reopen"), "fresh reopen receipt")
    fresh_program = _object(fresh.get("program_readback"), "fresh LD readback")
    fresh_check = _object(fresh.get("program_check"), "fresh Program Check")
    fresh_binding = _object(fresh.get("approval_binding"), "fresh approval binding")
    source_pid, source_hwnd = _field(target, "engineering_process_id"), _field(target, "engineering_window_handle")
    source_path = _field(target, "project_path")
    project_hash_before = _field(target, "artifact_hash")
    before_ld = application.get("payload_sha256_before")
    after_ld = application.get("payload_sha256_after")
    project_hash_after = saved.get("project_hash")
    for name, item in {
        "transaction id": transaction_id, "source project path": source_path,
        "source pid": source_pid, "source hwnd": source_hwnd,
        "reopened pid": fresh_reopen.get("pid"), "reopened hwnd": fresh_reopen.get("hwnd"),
    }.items():
        if item is None or item == "" or (name.endswith(("pid", "hwnd")) and (type(item) is not int or item <= 0)):
            raise OrchestrationError(f"terminal transaction has invalid {name}")
    for name, digest in {
        "project hash before": project_hash_before, "project hash after": project_hash_after,
        "reopened project hash": fresh_reopen.get("project_sha256"),
        "before LD": before_ld, "after LD": after_ld,
    }.items():
        _require_sha256(str(digest or ""), name)
    if fresh_reopen.get("source_pid") != source_pid or fresh_reopen.get("source_hwnd") != source_hwnd:
        raise OrchestrationError("fresh reopen source identity differs from transaction target")
    if fresh_reopen["pid"] == source_pid or fresh_reopen["hwnd"] == source_hwnd:
        raise OrchestrationError("fresh reopen did not replace the source PID/HWND")
    if fresh_reopen.get("project_path") != source_path:
        raise OrchestrationError("fresh reopen project path differs from source")
    if binding is not None:
        bound_workspace = _object(binding.get("workspace"), "proposal binding workspace")
        if (
            fresh_reopen.get("workspace_id") != bound_workspace.get("workspace_id")
            or not _same_path(fresh_reopen.get("workspace_root"), bound_workspace.get("workspace_root"))
        ):
            raise OrchestrationError("fresh reopen workspace root differs from persisted proposal binding")
    validation = _object(verification.get("validation"), "validation result")
    validation_check = _object(validation.get("program_check"), "saved Program Check")
    if validation_check.get("ok") is not True or validation_check.get("verified") is not True:
        raise OrchestrationError("Host validation did not prove Program Check")
    if fresh.get("operation") != "project.fresh_reopen_ld_approval_readback" or fresh.get("verified") is not True:
        raise OrchestrationError("fresh reopen CLI result is not verified LD approval evidence")
    if fresh_binding.get("approval_id") != str(transaction_id) or fresh_binding.get("approval_plan_digest") != plan_digest or fresh_binding.get("result_digest") != _canonical_digest(result):
        raise OrchestrationError("fresh reopen CLI receipt is not bound to Host approval/result")
    if fresh_program.get("ld_payload_sha256") != after_ld or fresh_program.get("program_id") != PROGRAM_ID or fresh_program.get("program_name") != PROGRAM_NAME:
        raise OrchestrationError("fresh LD readback does not match the approved saved result")
    if fresh_check.get("ok") is not True or fresh_check.get("verified") is not True or project_hash_after != fresh_reopen.get("project_sha256"):
        raise OrchestrationError("fresh reopen does not prove Program Check and saved project hash")
    if before_ld == after_ld:
        raise OrchestrationError("terminal evidence does not prove the LD operand changed")
    return {
        "schema": LIVE_RECEIPT_SCHEMA,
        "gate_id": "CA-04-CA-07",
        "status": "PASS",
        "captured_at": _utc_now(),
        "manifest_sha256": _require_sha256(manifest_sha256, "manifest sha256"),
        "evidence": {
            "approval_id": str(transaction_id),
            "approval_plan_sha256": plan_digest,
            "result_digest": _canonical_digest(result),
            "source": {"pid": source_pid, "hwnd": source_hwnd, "project_path": source_path},
            "reopened": {"pid": fresh_reopen["pid"], "hwnd": fresh_reopen["hwnd"], "project_path": fresh_reopen["project_path"], "project_sha256": fresh_reopen["project_sha256"]},
            "project_hash_before": project_hash_before,
            "project_hash_after": project_hash_after,
            "saved": True,
            "program_check": {"status": "PASS", "passed": True},
            "readback": {
                "kind": "LD",
                "before_ld_sha256": before_ld,
                "after_ld_sha256": after_ld,
            },
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, default=default_backup())
    parser.add_argument("--artifacts", type=Path, default=_repository_root() / "artifacts" / "ca04-ca07-one-shot")
    parser.add_argument("--base-url", default="http://127.0.0.1:8767")
    parser.add_argument("--launch-token-file", type=Path)
    parser.add_argument(
        "--xg-cli", type=Path,
        default=_repository_root() / "controlstudio-xg5000-cli" / ".venv" / "Scripts" / "controlstudio-xg5000.exe",
    )
    parser.add_argument("--prepare", action="store_true", help="create/open a new disposable Host workspace")
    parser.add_argument("--start-proposal", action="store_true", help="ask Agent to create the proposal and stop at approval")
    parser.add_argument("--approve-and-execute", action="store_true", help="approve and execute an existing exact Host transaction")
    parser.add_argument("--transaction-id")
    parser.add_argument("--plan-digest")
    parser.add_argument("--acceptance-manifest-sha256", help="exact SHA-256 printed by plc_agent_acceptance.py")
    parser.add_argument("--proposal-binding", type=Path, help="immutable binding written by --prepare --start-proposal")
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--proposal-timeout-seconds", type=int, default=300)
    parser.add_argument("--yes-disposable-ca04-ca07", action="store_true")
    args = parser.parse_args(argv)
    try:
        backup = args.backup.resolve()
        if not backup.is_file():
            raise OrchestrationError(f"backup does not exist: {backup}")
        if _sha256_file(backup) != BACKUP_SHA256:
            raise OrchestrationError("backup SHA-256 does not match the reviewed disposable artifact")
        artifact_dir = args.artifacts.resolve()
        contract = execution_contract(backup, artifact_dir)
        _write_json(artifact_dir / "execution-contract.json", contract)
        result: dict[str, Any] = {"contract": contract, "mode": "dry_run"}
        if args.prepare or args.start_proposal or args.approve_and_execute:
            if not args.yes_disposable_ca04_ca07:
                raise OrchestrationError("live phases require --yes-disposable-ca04-ca07")
            client = HostClient(args.base_url, _load_launch_token(args.launch_token_file))
            client.authenticate()
            if args.prepare:
                result["prepared"] = client.prepare_workspace(backup)
            if args.start_proposal:
                if not args.prepare:
                    raise OrchestrationError("--start-proposal requires --prepare in the same invocation")
                if args.proposal_timeout_seconds < 1 or args.proposal_timeout_seconds > 900:
                    raise OrchestrationError("--proposal-timeout-seconds must be 1..900")
                proposal = client.start_exact_proposal()
                conversation = _object(proposal.get("conversation"), "proposal conversation")
                conversation_id = _field(conversation, "conversation_id")
                if not isinstance(conversation_id, str) or not conversation_id:
                    raise OrchestrationError("proposal conversation has no identity")
                prepared = _object(result.get("prepared"), "prepared disposable workspace")
                prepared_workspace = _object(prepared.get("workspace"), "prepared workspace identity")
                prepared_workspace_id = _field(prepared_workspace, "workspace_id")
                if not isinstance(prepared_workspace_id, str) or not prepared_workspace_id:
                    raise OrchestrationError("prepared disposable workspace has no workspace identity")
                pending = client.wait_for_pending_approval(
                    conversation_id, prepared_workspace_id, args.proposal_timeout_seconds
                )
                submitted = _object(proposal.get("submission"), "proposal submission")
                binding = _proposal_binding(
                    prepared, pending, {**submitted, "conversationId": conversation_id}
                )
                binding_path = (args.proposal_binding or artifact_dir / "proposal-binding.json").resolve()
                _write_json(binding_path, binding)
                result["proposal_pending_approval"] = {
                    "conversation_id": conversation_id,
                    "transaction_id": _field(pending, "transaction_id"),
                    "plan_digest": _field(pending, "plan_digest"),
                    "proposal_binding": str(binding_path),
                    "transaction": pending,
                }
            if args.approve_and_execute:
                if not args.transaction_id or not args.plan_digest:
                    raise OrchestrationError("--approve-and-execute requires --transaction-id and --plan-digest")
                if args.timeout_seconds < 1 or args.timeout_seconds > 900:
                    raise OrchestrationError("--timeout-seconds must be 1..900")
                if not args.acceptance_manifest_sha256:
                    raise OrchestrationError("--approve-and-execute requires --acceptance-manifest-sha256")
                binding_path = (args.proposal_binding or artifact_dir / "proposal-binding.json").resolve()
                binding = _read_json_object(binding_path, "proposal binding")
                if not isinstance(binding.get("conversation_id"), str) or not isinstance(binding.get("run_id"), str):
                    raise OrchestrationError("proposal binding lacks conversation/run identity")
                client.wait_for_approval_wait(
                    binding["conversation_id"], binding["run_id"], binding, args.timeout_seconds
                )
                result["execution_started"] = client.approve_and_execute(
                    args.transaction_id, args.plan_digest, binding
                )
                terminal = client.wait_for_terminal(args.transaction_id, args.timeout_seconds)
                result["terminal_transaction"] = terminal
                fresh = run_ld_fresh_reopen(
                    args.xg_cli.resolve(), terminal, binding, timeout_seconds=args.timeout_seconds
                )
                result["fresh_reopen"] = fresh
                receipt = build_live_receipt(
                    terminal, fresh, args.acceptance_manifest_sha256, binding
                )
                _write_json(artifact_dir / "ca04-ca07.live-receipt.json", receipt)
                result["live_receipt"] = str((artifact_dir / "ca04-ca07.live-receipt.json").resolve())
            result["mode"] = "live_requested"
        _write_json(artifact_dir / "last-run.json", result)
        print(json.dumps({"mode": result["mode"], "contract": str((artifact_dir / "execution-contract.json").resolve()), "result": str((artifact_dir / "last-run.json").resolve())}, ensure_ascii=False))
        return 0
    except OrchestrationError as exc:
        print(json.dumps({"schema": "controlstudio-ca04-ca07-one-shot-error/v1", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
