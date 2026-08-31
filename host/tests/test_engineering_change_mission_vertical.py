from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from controlstudio_host.agent_runtime import (
    MAX_ENGINEERING_AUTONOMOUS_CYCLES,
    AgentOutputContractDiagnostic,
    AgentOutputContractError,
    AgentRunOutput,
)
from controlstudio_host.api_models import (
    EngineeringMissionSnapshotV1,
    canonical_engineering_mission_snapshot,
)
from controlstudio_host.app import create_app
from controlstudio_host.config import HostConfig
from controlstudio_host.execution_broker import MCPExecutionBroker
from controlstudio_host.mcp_client import MCPStatus
from controlstudio_host.model_connection import ModelConnectionRepository
from controlstudio_host.models import (
    ApprovalDecision,
    ChangeRiskLevel,
    ChangeTargetIdentity,
    ChangeTransactionState,
    MessageRole,
    Run,
    RunState,
)
from controlstudio_host.store import HostStore

_LAUNCH_TOKEN = "mission-vertical-test-launch-token"
_WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
_REVISION_ID = "22222222-2222-4222-8222-222222222222"
_SOURCE_HASH = "a" * 64
_WORKING_HASH = "b" * 64
_PLAN_DIGEST = "c" * 64
_XG5000_PID = 4101
_XG5000_HWND = 4201
_TERMINAL_STATES = {
    RunState.COMPLETED,
    RunState.CANCELLED,
    RunState.FAILED,
    RunState.QUARANTINED,
}
_CHANGE_OUTCOME_KEYS = {
    "schemaVersion",
    "transactionId",
    "planDigest",
    "state",
    "disposition",
    "evidenceId",
    "outcomeDigest",
    "verificationEvidenceDigest",
    "errorCode",
}


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_mission(value: dict[str, Any]) -> dict[str, Any]:
    return EngineeringMissionSnapshotV1.model_validate(value).model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )


def _ready_hardware_evidence_contract() -> dict[str, Any]:
    unsigned: dict[str, Any] = {
        "schema": "xg5000.project.hardware-context/1.0",
        "project_binding": {
            "session_id": "mission-fixture-xg5000-session",
            "pid": _XG5000_PID,
            "hwnd": _XG5000_HWND,
            "project_hash": _WORKING_HASH,
        },
        "cpu": {"status": "observed", "model": "XBC-DN/DP64H"},
        "timer_areas": [],
        "io_modules": [
            {
                "status": "observed",
                "rack_base": 0,
                "slot": 0,
                "module_id": 1,
            }
        ],
        "io_mapping": {"status": "observed", "channels": []},
        "external_wiring_observed": False,
        "blockers": [],
        "offline_candidate_ready": True,
    }
    return MCPExecutionBroker._hardware_context_evidence_contract(
        {**unsigned, "context_digest": _canonical_digest(unsigned)}
    )


def _ready_change_target_evidence_contract(
    hardware_context_digest: str,
    *,
    hardware_project_hash: str = _WORKING_HASH,
) -> dict[str, Any]:
    field_binding: dict[str, Any] = {
        "schema": "xg5000.project.field-io-binding/1.0",
        "device": "P0000",
        "direction": "input",
        "point_kind": "digital_input",
        "module": {"rack_base": 0, "slot": 0, "module_id": 1},
        "channel": {"index": 0, "status": "derived_from_verified_terminal_range"},
        "terminal_range": {"address_start": "P0000", "address_end": "P001F"},
        "proposal_scope": {"online_force_write_download": "unsupported"},
    }
    field_binding["binding_digest"] = _canonical_digest(field_binding)
    unsigned: dict[str, Any] = {
        "schema": "xg5000.program.change-target-analysis/1.1",
        "target": {
            "program_id": "program-1",
            "program_name": "MainProgram",
            "value_token_offset": 1,
            "value": "P0000",
            "record_kind": "CONTACT_NO",
            "visual_row": 0,
            "column": 0,
            "network_id": "network-1",
            "network_digest": "d" * 64,
            "network_index": 0,
            "network_semantic_status": "structural",
            "rung_id": "rung-1",
            "rung_role": "condition",
            "rung_semantic_status": "structural",
        },
        "current_semantics": {"status": "confirmed", "kind": "contact_operand"},
        "proposed_semantics": {"status": "confirmed", "kind": "contact_operand"},
        "proposed_value": "P0001",
        "project_binding": {
            "session_id": "mission-fixture-xg5000-session",
            "pid": _XG5000_PID,
            "hwnd": _XG5000_HWND,
            "project_hash": _WORKING_HASH,
        },
        "hardware_context_binding": {
            "schema": "xg5000.project.hardware-context/1.0",
            "context_digest": hardware_context_digest,
            "project_hash": hardware_project_hash,
            "cpu_status": "observed",
            "timer_area_status": "unobserved",
            "io_mapping_status": "observed",
            "external_wiring_observed": False,
            "external_wiring_status": "unobserved",
            "required_context": ["cpu", "io_mapping"],
            "blocking_reasons": [],
        },
        "impact": {
            "primary_devices": ["P0000"],
            "physical_io_candidates": ["P0000"],
            "physical_output_candidates": [],
            "multiple_writer_devices": [],
            "physical_io_hardware_bindings": [
                {
                    "device": "P0000",
                    "binding_status": "bound",
                    "field_io_binding": field_binding,
                }
            ],
        },
        "blocking_reasons": [],
        "offline_candidate_ready": True,
        "runtime_deployment_ready": False,
        "runtime_deployment_blocking_reasons": [
            "online_runtime_deployment_unsupported"
        ],
    }
    return MCPExecutionBroker._change_target_evidence_contract(
        {"analysis": {**unsigned, "analysis_digest": _canonical_digest(unsigned)}}
    )


def _ready_internal_m_change_target_evidence_contract() -> dict[str, Any]:
    project_binding = {
        "session_id": "mission-fixture-xg5000-session",
        "pid": _XG5000_PID,
        "hwnd": _XG5000_HWND,
        "project_hash": _WORKING_HASH,
    }
    contract: dict[str, Any] = {
        "schema": "controlstudio-change-target-evidence/v2",
        "analysisDigest": "a" * 64,
        "projectBindingDigest": _canonical_digest(project_binding),
        "programId": "program-1",
        "programName": "MainProgram",
        "valueTokenOffset": 1,
        "currentValue": "M0010",
        "proposedValue": "M0011",
        "recordKind": "CONTACT_NO",
        "visualRow": 0,
        "column": 0,
        "networkId": "network-1",
        "networkDigest": "d" * 64,
        "networkIndex": 0,
        "networkSemanticStatus": "structural",
        "rungId": "rung-1",
        "rungRole": "condition",
        "rungSemanticStatus": "structural",
        "currentSemantics": {
            "status": "confirmed",
            "kind": "contact_operand",
            "device": "M0010",
        },
        "proposedSemantics": {
            "status": "confirmed",
            "kind": "contact_operand",
            "device": "M0011",
        },
        "primaryDevices": ["M0010", "M0011"],
        "physicalIoCandidates": [],
        "physicalIoBindings": [],
        "physicalOutputCandidates": [],
        "multipleWriterDevices": [],
        "hardwareContextBinding": None,
        "offlineCandidateReady": True,
        "blockingReasons": [],
        "runtimeDeploymentReady": False,
        "runtimeDeploymentBlockingReasons": [
            "online_runtime_deployment_unsupported"
        ],
    }
    return {**contract, "receiptDigest": _canonical_digest(contract)}


def _workspace(root: Path) -> dict[str, Any]:
    source = root / "source" / "machine.xgwx"
    captured = root / "workspaces" / "source" / "machine.xgwx"
    working = root / "workspaces" / "working" / "machine.xgwx"
    for path, contents in (
        (source, b"immutable source"),
        (captured, b"captured source"),
        (working, b"working copy"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    return {
        "workspace_id": _WORKSPACE_ID,
        "revision_id": _REVISION_ID,
        "source_kind": "local_file",
        "vendor": "ls",
        "engineering_system": "XG5000",
        "created_at": "2026-08-16T00:00:00+00:00",
        "source": {
            "original_path": str(source.resolve()),
            "captured_path": str(captured.resolve()),
            "sha256": _SOURCE_HASH,
            "original_state": "unchanged",
            "original_current_sha256": _SOURCE_HASH,
        },
        "working_copy": {
            "path": str(working.resolve()),
            "baseline_sha256": _WORKING_HASH,
            "current_sha256": _WORKING_HASH,
            "state": "clean",
        },
    }


def _workspace_binding(workspace: dict[str, Any]) -> dict[str, Any]:
    return {
        "workspace_id": workspace["workspace_id"],
        "revision_id": workspace["revision_id"],
        "source_kind": workspace["source_kind"],
        "provenance_receipt_digest": None,
        "source_sha256": workspace["source"]["sha256"],
        "working_path": workspace["working_copy"]["path"],
        "working_sha256": workspace["working_copy"]["current_sha256"],
    }


class _WorkspaceMCP:
    """Only the exact static-workspace surface needed by this Host lifecycle."""

    def __init__(self, workspace: dict[str, Any]) -> None:
        self.workspace = workspace
        self.status = MCPStatus(
            status="ready",
            server_name="mission-fixture-mcp",
            server_version="1.0.0",
            protocol_version="2025-11-25",
            tool_count=80,
            agent_tool_count=4,
        )
        self._identity: dict[str, Any] | None = None

    @property
    def agent_session_identity_snapshot(self) -> dict[str, Any] | None:
        return dict(self._identity) if self._identity is not None else None

    async def start(self) -> MCPStatus:
        return self.status

    async def stop(self) -> None:
        return None

    def bind_agent_workspace(self, workspace: dict[str, Any]) -> None:
        self._identity = dict(workspace)

    def bind_agent_session(self, session: dict[str, Any]) -> None:
        self._identity = dict(session)

    def clear_agent_session(self) -> None:
        self._identity = None

    def clear_controller_endpoint(self) -> None:
        return None

    async def call_read_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if name != "xg5000.attach":
            raise AssertionError(f"unexpected read call: {name} {arguments}")
        working = self.workspace["working_copy"]
        if arguments != {
            "pid": _XG5000_PID,
            "hwnd": _XG5000_HWND,
            "access_profile": "analysis",
            "project_path": working["path"],
            "project_hash": working["current_sha256"],
        }:
            raise AssertionError(f"unexpected attach arguments: {arguments}")
        return {
            "schema_version": "1",
            "session_id": "mission-fixture-xg5000-session",
            "pid": _XG5000_PID,
            "hwnd": _XG5000_HWND,
            "stage": "ANALYSIS_READY",
            "project_path": working["path"],
            "project_hash": working["current_sha256"],
            "metadata": {
                "project_identity_verified_against_selected_pid": True,
                "project_identity_read_only_bound_to_selected_pid": True,
                "project_identity_assurance": "controlled_launch_read_only",
                "project_identity_proof": {"process_create_time": 1_700_000_000.0},
                "controlled_open_read_proof": {
                    "method": "controlled_open_verified_cli_receipt_v3",
                    "proof_source": "verified_workspace_launch_cli",
                    "pid": _XG5000_PID,
                    "main_hwnd": _XG5000_HWND,
                    "workspace_id": _WORKSPACE_ID,
                    "window_title": "Mission fixture XG5000",
                    "process_create_time": 1_700_000_000.0,
                    "project_path": working["path"],
                    "project_hash": working["current_sha256"],
                },
            },
        }

    async def call_workspace_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if name != "workspace.status" or arguments != {"workspace_id": _WORKSPACE_ID}:
            raise AssertionError(f"unexpected workspace call: {name} {arguments}")
        return {"ok": True, "verified": True, "operation": name, **self.workspace}

    def mne_capability_status(self) -> str:
        return "missing"


def _authenticate(client: TestClient, config: HostConfig) -> None:
    handoff = client.post(
        "/api/launcher/handoff",
        headers={"Authorization": f"Bearer {config.launch_token}"},
    )
    if handoff.status_code != 201:
        raise AssertionError(handoff.text)
    session = client.post(
        "/api/session",
        headers={"Authorization": f"Bearer {handoff.json()['handoffTicket']}"},
    )
    if session.status_code != 200:
        raise AssertionError(session.text)
    client.headers.update({"Authorization": f"Bearer {session.json()['sessionToken']}"})


def _wait_for_workspace_binding(mcp: _WorkspaceMCP) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if mcp.agent_session_identity_snapshot is not None:
            return
        time.sleep(0.01)
    raise AssertionError("Host did not restore the persisted workspace binding")


def _attach_workspace(client: TestClient, workspace: dict[str, Any]) -> None:
    attached = client.post(
        "/api/v1/xg5000/attach",
        json={
            "pid": _XG5000_PID,
            "hwnd": _XG5000_HWND,
            "projectPath": workspace["working_copy"]["path"],
            "projectHash": workspace["working_copy"]["current_sha256"],
        },
    )
    if attached.status_code != 200:
        raise AssertionError(attached.text)


def _wait_for_run(store: HostStore, run_id: str) -> Run:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        run = store.get_run(run_id)
        if run is not None and run.state in _TERMINAL_STATES:
            return run
        time.sleep(0.01)
    raise AssertionError(f"Agent run did not become terminal: {run_id}")


def _mission(
    *,
    objective: str,
    status: str,
    outcome_evidence_id: str | None = None,
    fresh_target_evidence_id: str | None = None,
) -> dict[str, Any]:
    verify_step: dict[str, Any] = {
        "stepId": "verify-change-outcome",
        "title": "Verify the Host-owned change outcome",
        "status": "pending",
        "outcome": None,
        "evidenceRefs": [],
    }
    if status == "completed":
        if outcome_evidence_id is None or fresh_target_evidence_id is None:
            raise AssertionError(
                "completed fixture mission needs outcome and fresh target evidence"
            )
        verify_step.update(
            status="completed",
            outcome="The Host returned a verified terminal change outcome.",
            evidenceRefs=[outcome_evidence_id, fresh_target_evidence_id],
        )
    values: dict[str, Any] = {
        "schemaVersion": "1",
        "objective": objective,
        "status": status,
        "completionCriteria": ["The proposed change reaches a Host-verified terminal outcome."],
        "steps": [
            {
                "stepId": "propose-change",
                "title": "Create one reviewable change proposal",
                "status": "completed",
                "outcome": "One digest-bound proposal was created.",
                "evidenceRefs": [],
            },
            verify_step,
        ],
        "activeStepId": None,
        "blockingQuestions": [],
        "nextAction": None,
        "stopReason": "needs_human_approval",
    }
    if status == "waiting_for_approval":
        values["nextAction"] = "Wait for the exact Host transaction decision."
    elif status == "waiting_for_user":
        values.update(
            blockingQuestions=["Should the Agent ignore the pending approval contract?"],
            stopReason="needs_user_input",
        )
    elif status == "completed":
        values["stopReason"] = "objective_achieved"
    else:
        raise AssertionError(f"unsupported fixture mission status: {status}")
    return values


class _MissionScenario:
    def __init__(
        self,
        *,
        invalid_after_proposal: bool = False,
        terminal_failures_before_checkpoint: int = 0,
        capture_mne_planner_only: bool = False,
        fail_engineering_analysis_output: bool = False,
    ) -> None:
        self.invalid_after_proposal = invalid_after_proposal
        self.terminal_failures_before_checkpoint = terminal_failures_before_checkpoint
        self.capture_mne_planner_only = capture_mne_planner_only
        self.fail_engineering_analysis_output = fail_engineering_analysis_output
        self.store: HostStore | None = None
        self.workspace: dict[str, Any] | None = None
        self.runtime_calls = 0
        self.transaction_id: str | None = None
        self.change_outcomes: list[dict[str, Any]] = []
        self.outcome_evidence_seen_before_runtime: list[bool] = []
        self.contract_errors: list[str] = []
        self.mne_planner_available: list[bool] = []

    def bind_store(self, store: HostStore, workspace: dict[str, Any]) -> None:
        self.store = store
        self.workspace = workspace

    def runtime_factory(self, _settings: Any, _broker: Any) -> _ScenarioRuntime:
        return _ScenarioRuntime(self)

    def create_pending_change(self, conversation_id: str, run_id: str) -> None:
        if self.store is None or self.workspace is None:
            raise AssertionError("scenario store was not bound")
        detail = self.store.get_conversation(conversation_id)
        if detail is None or detail.engineering_session_id is None:
            raise AssertionError("engineering conversation target was not bound")
        target = ChangeTargetIdentity(
            workspace_id=_WORKSPACE_ID,
            revision_id=_REVISION_ID,
            artifact_hash=_WORKING_HASH,
            source_artifact_hash=_SOURCE_HASH,
            project_path=self.workspace["working_copy"]["path"],
            vendor="ls",
            engineering_system="XG5000",
            engineering_session_id=detail.engineering_session_id,
        )
        transaction, created = self.store.create_change_transaction(
            operation_id=f"agent-proposal:{run_id}",
            conversation_id=conversation_id,
            run_id=run_id,
            target=target,
            mutation_scope=f"workspace:{_WORKSPACE_ID}",
            risk_level=ChangeRiskLevel.HIGH,
            intent="Replace one reviewed offline operand",
            plan={"schema": "mission-change-fixture/v1"},
            plan_digest=_PLAN_DIGEST,
            requested_by_actor_id="mission-fixture-agent",
            approval_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        if not created:
            raise AssertionError("the scenario replayed its change proposal")
        self.transaction_id = transaction.id


class _ScenarioRuntime:
    def __init__(self, scenario: _MissionScenario) -> None:
        self.scenario = scenario

    def bind_change_planner(self, _callback: Any) -> None:
        return None

    async def run(self, **kwargs: Any) -> AgentRunOutput:
        self.scenario.runtime_calls += 1
        call_number = self.scenario.runtime_calls
        if self.scenario.fail_engineering_analysis_output:
            raise AgentOutputContractError(
                "fixture engineering-analysis output rejected",
                diagnostic=AgentOutputContractDiagnostic(
                    stage="engineering_analysis",
                    exception_kind="model_retry",
                    reason_code="validator_rejected",
                    validator_code="analysis_contract",
                    validation_errors=(),
                ),
            )
        trusted_context = kwargs["trusted_context"]
        self.scenario.mne_planner_available.append(
            trusted_context.get("mne_change_planner_available") is True
        )
        if self.scenario.capture_mne_planner_only:
            mission = _canonical_mission(
                _mission(objective=kwargs["prompt"], status="waiting_for_user")
            )
            digest = _canonical_digest(mission)
            checkpoint = {
                "schemaVersion": "1",
                "cycle": 1,
                "missionDigest": digest,
                "messages": [],
            }
            await kwargs["on_mission_checkpoint"](
                mission,
                digest,
                1,
                "respond",
                checkpoint,
                _canonical_digest(checkpoint),
            )
            return AgentRunOutput(
                content="The offline planning tools are available.",
                model="deterministic-mission-fixture",
                input_tokens=1,
                output_tokens=1,
                engineering_mission=mission,
                digest=digest,
                cycle_disposition="respond",
            )
        if "change_outcome" in trusted_context:
            change_outcome = trusted_context.get("change_outcome")
            if not isinstance(change_outcome, dict):
                raise AssertionError("terminal continuation omitted change_outcome")
            self.scenario.change_outcomes.append(dict(change_outcome))
            if self.scenario.store is None:
                raise AssertionError("scenario store was not bound")
            evidence_id = change_outcome.get("evidenceId")
            evidence = self.scenario.store.list_run_evidence(
                conversation_id=kwargs["conversation_id"],
                run_id=kwargs["run_id"],
            )
            self.scenario.outcome_evidence_seen_before_runtime.append(
                isinstance(evidence_id, str)
                and any(item.payload.get("evidenceId") == evidence_id for item in evidence)
            )
            if self.scenario.terminal_failures_before_checkpoint:
                self.scenario.terminal_failures_before_checkpoint -= 1
                raise RuntimeError("simulated failure before the terminal checkpoint")
            previous = trusted_context.get("engineering_mission")
            if not isinstance(previous, dict):
                raise AssertionError("terminal continuation omitted the durable mission")
            fresh_target_evidence = await kwargs["on_tool_event"](
                "tool.completed",
                {
                    "tool_name": "xg5000.session_status",
                    "request_id": "fresh-post-change-target-read",
                    "tool_call_id": "fresh-post-change-target-read",
                },
            )
            if not isinstance(fresh_target_evidence, dict):
                raise AssertionError("fresh post-change target read returned no evidence")
            fresh_target_evidence_id = fresh_target_evidence.get("evidenceId")
            if not isinstance(fresh_target_evidence_id, str):
                raise AssertionError("fresh post-change target evidence id is invalid")
            mission = _canonical_mission(
                _mission(
                    objective=previous["objective"],
                    status="completed",
                    outcome_evidence_id=str(evidence_id),
                    fresh_target_evidence_id=fresh_target_evidence_id,
                )
            )
        elif call_number == 1:
            if "change_outcome" in trusted_context:
                raise AssertionError("an initial run received a change outcome")
            self.scenario.create_pending_change(
                kwargs["conversation_id"],
                kwargs["run_id"],
            )
            objective = kwargs["prompt"]
            status = (
                "waiting_for_user"
                if self.scenario.invalid_after_proposal
                else "waiting_for_approval"
            )
            mission = _canonical_mission(_mission(objective=objective, status=status))
        else:
            raise AssertionError("the mission runtime was replayed")

        digest = _canonical_digest(mission)
        checkpoint = {
            "schemaVersion": "1",
            "cycle": 1,
            "missionDigest": digest,
            "messages": [],
        }
        try:
            await kwargs["on_mission_checkpoint"](
                mission,
                digest,
                1,
                "respond",
                checkpoint,
                _canonical_digest(checkpoint),
            )
        except Exception as exc:
            self.scenario.contract_errors.append(f"{type(exc).__name__}: {exc}")
            raise
        return AgentRunOutput(
            content=(
                "The verified change outcome completes the mission."
                if mission["status"] == "completed"
                else "The change proposal is waiting for its Host-owned decision."
            ),
            model="deterministic-mission-fixture",
            input_tokens=1,
            output_tokens=1,
            engineering_mission=mission,
            digest=digest,
            cycle_disposition="respond",
        )


class _ReadyLdProposalScenario(_MissionScenario):
    def __init__(
        self,
        *,
        mismatched_host_owned_hash: bool = False,
        reject_final_artifact: bool = False,
        internal_m_without_hardware: bool = False,
    ) -> None:
        super().__init__()
        self.mismatched_host_owned_hash = mismatched_host_owned_hash
        self.reject_final_artifact = reject_final_artifact
        self.internal_m_without_hardware = internal_m_without_hardware
        self.proposal: dict[str, Any] | None = None

    def runtime_factory(
        self,
        _settings: Any,
        _broker: Any,
    ) -> _ReadyLdProposalRuntime:
        return _ReadyLdProposalRuntime(self)


class _ReadyLdProposalRuntime:
    def __init__(self, scenario: _ReadyLdProposalScenario) -> None:
        self.scenario = scenario
        self.planner: Any = None

    def bind_change_planner(self, callback: Any) -> None:
        self.planner = callback

    async def _publish(
        self,
        on_tool_event: Any,
        tool_name: str,
        tool_call_id: str,
        *,
        contract: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tool_name": tool_name,
            "request_id": tool_call_id,
            "tool_call_id": tool_call_id,
        }
        if contract is not None:
            payload["evidence_contract"] = contract
        evidence = await on_tool_event("tool.completed", payload)
        if not isinstance(evidence, dict):
            raise TypeError(f"{tool_name} returned no durable evidence")
        return evidence

    async def run(self, **kwargs: Any) -> AgentRunOutput:
        self.scenario.runtime_calls += 1
        if self.scenario.runtime_calls != 1:
            raise AssertionError("ready LD proposal runtime was replayed")
        if not callable(self.planner):
            raise TypeError("Host did not bind the offline change planner")

        if self.scenario.internal_m_without_hardware:
            analysis = _ready_internal_m_change_target_evidence_contract()
            analysis_evidence = await self._publish(
                kwargs["on_tool_event"],
                "program.change_target_analysis",
                "ready-internal-m-target-analysis",
                contract=analysis,
            )
            snapshot_evidence = await self._publish(
                kwargs["on_tool_event"],
                "project.snapshot",
                "ready-internal-m-snapshot",
            )
            analysis_id = str(analysis_evidence["evidenceId"])
            snapshot_id = str(snapshot_evidence["evidenceId"])
            target = {
                "analysisEvidenceRef": analysis_id,
                "analysisDigest": analysis["analysisDigest"],
                "analysisReceiptDigest": analysis["receiptDigest"],
                "projectBindingDigest": analysis["projectBindingDigest"],
                "programId": analysis["programId"],
                "tokenOffset": analysis["valueTokenOffset"],
                "recordKind": analysis["recordKind"],
                "visualRow": analysis["visualRow"],
                "column": analysis["column"],
                "networkId": analysis["networkId"],
                "networkDigest": analysis["networkDigest"],
                "networkIndex": analysis["networkIndex"],
                "rungId": analysis["rungId"],
                "rungRole": analysis["rungRole"],
                "primaryDevices": analysis["primaryDevices"],
                "physicalIoCandidates": [],
                "physicalIoBindings": [],
                "physicalOutputCandidates": [],
                "multipleWriterDevices": [],
                "hardwareContextBinding": None,
                "runtimeDeploymentReady": analysis["runtimeDeploymentReady"],
                "runtimeDeploymentBlockingReasons": analysis[
                    "runtimeDeploymentBlockingReasons"
                ],
            }
            coverage = [
                {
                    "area": area,
                    "status": "confirmed",
                    "summary": f"{area} is bound to the native target receipt.",
                    "evidence_refs": refs,
                }
                for area, refs in (
                    ("target_identity", [analysis_id]),
                    ("value_semantics", [analysis_id]),
                    ("direct_impact", [analysis_id]),
                    ("indirect_impact", [analysis_id]),
                    ("safety", [analysis_id]),
                    ("validation", [snapshot_id]),
                    ("rollback", [snapshot_id]),
                )
            ]
            coverage.extend(
                {
                    "area": area,
                    "status": "not_applicable_with_reason",
                    "summary": "This internal-M candidate does not change this area.",
                    "evidence_refs": [],
                }
                for area in ("modes", "runtime", "external_integrations")
            )
            self.scenario.proposal = await self.planner(
                intent="Replace one confirmed internal M contact operand",
                risk_level="medium",
                change_mode="offline_working_copy_revision",
                target=target,
                current={
                    "value": analysis["currentValue"],
                    "semantics": analysis["currentSemantics"],
                },
                proposed={
                    "value": analysis["proposedValue"],
                    "semantics": analysis["proposedSemantics"],
                },
                impact=["One internal memory contact changes in the working copy."],
                risks=["The replacement must remain within the confirmed network."],
                assumptions=["No physical I/O candidate is present in the receipt."],
                blocking_unknowns=[],
                alternatives=["Retain M0010 and make no offline revision."],
                acceptance_criteria=["Native structural diff contains only M0010 to M0011."],
                observation_signals=["M0011 is reviewed after explicit approval."],
                stop_conditions=["Stop on any native Program Check error."],
                coverage=coverage,
                verification_scope="offline_native_reopen",
                validation=[
                    "structural_diff",
                    "xg5000_program_check",
                    "project_save",
                    "project_reopen",
                ],
                manual_follow_up=["A human must approve before any offline mutation."],
                rollback=["Discard the working revision and retain the clean baseline."],
            )
            self.scenario.transaction_id = str(self.scenario.proposal["transaction_id"])
            mission = _canonical_mission(
                _mission(objective=kwargs["prompt"], status="waiting_for_approval")
            )
            digest = _canonical_digest(mission)
            checkpoint = {
                "schemaVersion": "1",
                "cycle": 1,
                "missionDigest": digest,
                "messages": [],
            }
            await kwargs["on_mission_checkpoint"](
                mission,
                digest,
                1,
                "respond",
                checkpoint,
                _canonical_digest(checkpoint),
            )
            return AgentRunOutput(
                content="The exact internal-M proposal is waiting for human approval.",
                model="deterministic-ready-internal-m-fixture",
                input_tokens=1,
                output_tokens=1,
                engineering_mission=mission,
                digest=digest,
                cycle_disposition="respond",
            )

        hardware = _ready_hardware_evidence_contract()
        analysis = _ready_change_target_evidence_contract(
            hardware["contextDigest"],
            hardware_project_hash=(
                "e" * 64
                if self.scenario.mismatched_host_owned_hash
                else _WORKING_HASH
            ),
        )
        hardware_evidence = await self._publish(
            kwargs["on_tool_event"],
            "project.hardware_context",
            "ready-hardware-context",
            contract=hardware,
        )
        analysis_evidence = await self._publish(
            kwargs["on_tool_event"],
            "program.change_target_analysis",
            "ready-change-target-analysis",
            contract=analysis,
        )
        snapshot_evidence = await self._publish(
            kwargs["on_tool_event"],
            "project.snapshot",
            "ready-project-snapshot",
        )
        analysis_id = str(analysis_evidence["evidenceId"])
        hardware_id = str(hardware_evidence["evidenceId"])
        snapshot_id = str(snapshot_evidence["evidenceId"])
        hardware_context = {
            "cpu": hardware["cpu"],
            "timerAreas": hardware["timerAreas"],
            "ioModules": hardware["ioModules"],
            "ioMapping": hardware["ioMapping"],
            "externalWiringObserved": hardware["externalWiringObserved"],
            "blockers": hardware["blockers"],
            "offlineCandidateReady": hardware["offlineCandidateReady"],
        }
        target = {
            "analysisEvidenceRef": analysis_id,
            "analysisDigest": analysis["analysisDigest"],
            "analysisReceiptDigest": analysis["receiptDigest"],
            "projectBindingDigest": analysis["projectBindingDigest"],
            "programId": analysis["programId"],
            "tokenOffset": analysis["valueTokenOffset"],
            "recordKind": analysis["recordKind"],
            "visualRow": analysis["visualRow"],
            "column": analysis["column"],
            "networkId": analysis["networkId"],
            "networkDigest": analysis["networkDigest"],
            "networkIndex": analysis["networkIndex"],
            "rungId": analysis["rungId"],
            "rungRole": analysis["rungRole"],
            "primaryDevices": analysis["primaryDevices"],
            "physicalIoCandidates": analysis["physicalIoCandidates"],
            "physicalIoBindings": analysis["physicalIoBindings"],
            "physicalOutputCandidates": analysis["physicalOutputCandidates"],
            "multipleWriterDevices": analysis["multipleWriterDevices"],
            "hardwareContextBinding": {
                key: value
                for key, value in analysis["hardwareContextBinding"].items()
                if key != "project_hash"
            },
            "runtimeDeploymentReady": analysis["runtimeDeploymentReady"],
            "runtimeDeploymentBlockingReasons": analysis[
                "runtimeDeploymentBlockingReasons"
            ],
            "hardwareEvidenceRef": hardware_id,
            "hardwareContextDigest": hardware["contextDigest"],
            "hardwareReceiptDigest": hardware["receiptDigest"],
            "hardwareProjectBindingDigest": hardware["projectBindingDigest"],
            "hardwareContext": hardware_context,
        }
        analysis_and_hardware = [analysis_id, hardware_id]
        coverage = [
            {
                "area": area,
                "status": "confirmed",
                "summary": f"{area} is bound to native receipts.",
                "evidence_refs": refs,
            }
            for area, refs in (
                ("target_identity", analysis_and_hardware),
                ("value_semantics", analysis_and_hardware),
                ("direct_impact", analysis_and_hardware),
                ("indirect_impact", [analysis_id]),
                ("safety", analysis_and_hardware),
                ("validation", [snapshot_id]),
                ("rollback", [snapshot_id]),
            )
        ]
        coverage.extend(
            {
                "area": area,
                "status": "not_applicable_with_reason",
                "summary": "This one-operand offline candidate does not change this area.",
                "evidence_refs": [],
            }
            for area in ("modes", "runtime", "external_integrations")
        )
        self.scenario.proposal = await self.planner(
            intent="Replace one observed physical input contact operand",
            risk_level="high",
            change_mode="offline_working_copy_revision",
            target=target,
            current={
                "value": analysis["currentValue"],
                "semantics": analysis["currentSemantics"],
            },
            proposed={
                "value": analysis["proposedValue"],
                "semantics": analysis["proposedSemantics"],
            },
            impact=["One observed physical input contact changes its offline address."],
            risks=["The proposed address could represent a different field terminal."],
            assumptions=["The observed module and terminal mapping remain current."],
            blocking_unknowns=[],
            alternatives=["Retain P0000 and make no offline revision."],
            acceptance_criteria=["Native structural diff contains only the operand change."],
            observation_signals=["P0001 contact state is reviewed after explicit approval."],
            stop_conditions=["Stop on any native Program Check error."],
            coverage=coverage,
            verification_scope="offline_native_reopen",
            validation=[
                "structural_diff",
                "xg5000_program_check",
                "project_save",
                "project_reopen",
            ],
            manual_follow_up=["A human must approve before any offline mutation."],
            rollback=["Discard the working revision and retain the clean baseline."],
        )
        self.scenario.transaction_id = str(self.scenario.proposal["transaction_id"])
        mission = _canonical_mission(
            _mission(objective=kwargs["prompt"], status="waiting_for_approval")
        )
        digest = _canonical_digest(mission)
        checkpoint = {
            "schemaVersion": "1",
            "cycle": 1,
            "missionDigest": digest,
            "messages": [],
        }
        await kwargs["on_mission_checkpoint"](
            mission,
            digest,
            1,
            "respond",
            checkpoint,
            _canonical_digest(checkpoint),
        )
        engineering_analysis = None
        engineering_analysis_digest = None
        if self.scenario.reject_final_artifact:
            engineering_analysis = {
                "schemaVersion": "1",
                "observations": [],
                "inferences": [],
                "hypotheses": [],
                "alternatives": [],
                "unknowns": [],
                "nextActions": [],
                "evidenceRefs": [],
            }
            engineering_analysis_digest = _canonical_digest(engineering_analysis)
        return AgentRunOutput(
            content="The exact ready LD proposal is waiting for human approval.",
            model="deterministic-ready-ld-proposal-fixture",
            input_tokens=1,
            output_tokens=1,
            engineering_mission=mission,
            digest=digest,
            cycle_disposition="respond",
            engineering_analysis=engineering_analysis,
            engineering_analysis_digest=engineering_analysis_digest,
        )


class _HistoricalEvidenceScenario(_MissionScenario):
    def __init__(self) -> None:
        super().__init__()
        self.tool_completions = 0
        self.first_run_id: str | None = None
        self.first_evidence: dict[str, Any] | None = None
        self.first_mission_digest: str | None = None
        self.resumed_bundle: dict[str, Any] | None = None
        self.resumed_checkpoint_seen = False

    def runtime_factory(
        self,
        _settings: Any,
        _broker: Any,
    ) -> _HistoricalEvidenceRuntime:
        return _HistoricalEvidenceRuntime(self)


def _historical_mission(
    *,
    objective: str,
    evidence_id: str,
    stop_reason: str,
) -> dict[str, Any]:
    waiting_for_user = stop_reason == "needs_user_input"
    return _canonical_mission(
        {
            "schemaVersion": "1",
            "objective": objective,
            "status": "waiting_for_user" if waiting_for_user else "active",
            "completionCriteria": ["The selected target is inspected once."],
            "steps": [
                {
                    "stepId": "inspect-target-once",
                    "title": "Inspect the selected target once",
                    "status": "pending" if waiting_for_user else "active",
                    "outcome": None,
                    "evidenceRefs": [evidence_id],
                }
            ],
            "activeStepId": None if waiting_for_user else "inspect-target-once",
            "blockingQuestions": (
                ["Should the next distinct diagnostic step proceed?"]
                if waiting_for_user
                else []
            ),
            "nextAction": (
                None
                if waiting_for_user
                else "Continue from the retained target evidence."
            ),
            "stopReason": stop_reason,
        }
    )


class _HistoricalEvidenceRuntime:
    def __init__(self, scenario: _HistoricalEvidenceScenario) -> None:
        self.scenario = scenario

    async def run(self, **kwargs: Any) -> AgentRunOutput:
        self.scenario.runtime_calls += 1
        engineering_analysis: dict[str, Any] | None = None
        engineering_analysis_digest: str | None = None
        if self.scenario.runtime_calls == 1:
            self.scenario.first_run_id = kwargs["run_id"]
            evidence = await kwargs["on_tool_event"](
                "tool.completed",
                {
                    "tool_name": "xg5000.session_status",
                    "request_id": "durable-target-read",
                    "tool_call_id": "durable-target-read",
                },
            )
            if not isinstance(evidence, dict):
                raise AssertionError("the durable target read returned no evidence")
            self.scenario.tool_completions += 1
            self.scenario.first_evidence = dict(evidence)
            evidence_id = str(evidence["evidenceId"])
            mission: dict[str, Any] | None = None
            digest: str | None = None
            for cycle in range(1, MAX_ENGINEERING_AUTONOMOUS_CYCLES + 1):
                stop_reason = (
                    "cycle_budget_exhausted"
                    if cycle == MAX_ENGINEERING_AUTONOMOUS_CYCLES
                    else "continuing"
                )
                mission = _historical_mission(
                    objective=kwargs["prompt"],
                    evidence_id=evidence_id,
                    stop_reason=stop_reason,
                )
                digest = _canonical_digest(mission)
                checkpoint = {
                    "schemaVersion": "1",
                    "cycle": cycle,
                    "missionDigest": digest,
                    "messages": [],
                }
                await kwargs["on_mission_checkpoint"](
                    mission,
                    digest,
                    cycle,
                    "respond" if stop_reason == "cycle_budget_exhausted" else "continue",
                    checkpoint,
                    _canonical_digest(checkpoint),
                )
            assert mission is not None and digest is not None
            self.scenario.first_mission_digest = digest
        elif self.scenario.runtime_calls == 2:
            if not isinstance(kwargs.get("agent_checkpoint"), dict):
                raise AssertionError("Host restart omitted the durable Agent checkpoint")
            self.scenario.resumed_checkpoint_seen = True
            bundle = kwargs["trusted_context"].get("historical_evidence")
            if not isinstance(bundle, dict):
                raise AssertionError("Host restart omitted durable historical evidence")
            self.scenario.resumed_bundle = bundle
            evidence = self.scenario.first_evidence
            if evidence is None:
                raise AssertionError("the first evidence was not retained by the scenario")
            mission = _historical_mission(
                objective=kwargs["trusted_context"]["engineering_mission"]["objective"],
                evidence_id=str(evidence["evidenceId"]),
                stop_reason="needs_user_input",
            )
            digest = _canonical_digest(mission)
            checkpoint = {
                "schemaVersion": "1",
                "cycle": 1,
                "missionDigest": digest,
                "messages": [],
            }
            await kwargs["on_mission_checkpoint"](
                mission,
                digest,
                1,
                "respond",
                checkpoint,
                _canonical_digest(checkpoint),
            )
            engineering_analysis = {
                "schemaVersion": "1",
                "observations": [],
                "inferences": [
                    {
                        "statement": "The prior target read remains part of mission history.",
                        "confidence": "high",
                        "evidenceRefs": [str(evidence["evidenceId"])],
                    }
                ],
                "hypotheses": [],
                "alternatives": [],
                "unknowns": [],
                "nextActions": [],
                "evidenceRefs": [],
            }
            engineering_analysis_digest = _canonical_digest(engineering_analysis)
        else:
            raise AssertionError("historical evidence runtime was replayed")
        return AgentRunOutput(
            content="The prior target evidence was reused without another tool call.",
            model="deterministic-historical-evidence-fixture",
            input_tokens=1,
            output_tokens=1,
            engineering_mission=mission,
            digest=digest,
            cycle_disposition="respond",
            engineering_analysis=engineering_analysis,
            engineering_analysis_digest=engineering_analysis_digest,
        )


class EngineeringChangeMissionVerticalTests(unittest.TestCase):
    def _config(self, root: Path) -> HostConfig:
        return HostConfig(
            database_path=root / "host.sqlite3",
            knowledge_database_path=root / "knowledge.sqlite3",
            static_dir=root / "missing-web-dist",
            event_poll_interval_seconds=0.01,
            event_heartbeat_seconds=0.05,
            launch_token=_LAUNCH_TOKEN,
        )

    def _repository(
        self,
        store: HostStore,
    ) -> ModelConnectionRepository:
        repository = ModelConnectionRepository(store)
        if repository.get() is None:
            repository.save(
                base_url="http://localhost:4000/v1",
                model="deterministic-mission-fixture",
                timeout_seconds=30,
                api_key="fixture-key-not-sent-to-a-model",
                custom_headers={},
                capability_verified_at=datetime.now(timezone.utc),
                allow_insecure_http=True,
            )
        return repository

    def _app(
        self,
        *,
        config: HostConfig,
        store: HostStore,
        workspace: dict[str, Any],
        scenario: _MissionScenario,
    ) -> tuple[Any, _WorkspaceMCP]:
        scenario.bind_store(store, workspace)
        mcp = _WorkspaceMCP(workspace)
        app = create_app(
            config,
            store=store,
            mcp=mcp,  # type: ignore[arg-type]
            model_repository=self._repository(store),
            runtime_factory=scenario.runtime_factory,
        )
        return app, mcp

    def _create_conversation(self, client: TestClient) -> str:
        created = client.post(
            "/api/v1/conversations",
            json={"title": "Durable change mission"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        return str(created.json()["conversationId"])

    def _start_mission(
        self,
        client: TestClient,
        conversation_id: str,
        *,
        operation_id: str,
    ) -> str:
        response = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers={"Idempotency-Key": operation_id},
            json={
                "content": "Create one reviewed offline change and verify its outcome.",
                "operationId": operation_id,
                "changeRequestScope": "offline_candidate_revision",
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        return str(response.json()["runId"])

    def _seed_waiting_mission(
        self,
        *,
        store: HostStore,
        scenario: _MissionScenario,
        conversation_id: str,
        operation_id: str,
    ) -> str:
        _message, run, created = store.create_user_message_and_run(
            conversation_id=conversation_id,
            content="Create one reviewed offline change and verify its outcome.",
            operation_id=operation_id,
            workflow="engineering",
            message_metadata={"workflow": "engineering"},
            run_metadata={"workflow": "engineering"},
        )
        self.assertTrue(created)
        self.assertIsNotNone(run)
        assert run is not None
        scenario.create_pending_change(conversation_id, run.id)
        self.assertIsNotNone(scenario.transaction_id)
        mission = _canonical_mission(
            _mission(
                objective="Create one reviewed offline change and verify its outcome.",
                status="waiting_for_approval",
            )
        )
        mission_digest = _canonical_digest(mission)
        checkpoint = {
            "schemaVersion": "1",
            "cycle": 1,
            "missionDigest": mission_digest,
            "messages": [],
        }
        store.checkpoint_engineering_mission(
            conversation_id=conversation_id,
            run_id=run.id,
            cycle=1,
            cycle_disposition="respond",
            engineering_mission=mission,
            digest=mission_digest,
            agent_checkpoint=checkpoint,
            agent_checkpoint_digest=_canonical_digest(checkpoint),
            change_transaction_id=scenario.transaction_id,
            final_message_content="The change proposal is waiting for approval.",
            final_message_metadata={
                "workflow": "engineering",
                "engineering_mission": mission,
                "engineering_mission_digest": mission_digest,
            },
        )
        completed = store.update_run_state(run_id=run.id, state=RunState.COMPLETED)
        self.assertIsNotNone(completed)
        return run.id

    def _assert_resume_blocked(
        self,
        client: TestClient,
        store: HostStore,
        conversation_id: str,
        state: ChangeTransactionState,
    ) -> None:
        operation_id = f"resume-{state.value.casefold().replace('_', '-')}"
        response = client.post(
            f"/api/v1/conversations/{conversation_id}/engineering-mission/resume",
            headers={"Idempotency-Key": operation_id},
            json={"operationId": operation_id},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIsNone(store.get_run_by_operation_id(operation_id))

    def test_user_reverify_reopens_only_blocked_step_without_starting_agent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-blocked-step-reverify-") as temporary:
            root = Path(temporary)
            config = self._config(root)
            workspace = _workspace(root)
            store = HostStore(config.database_path)
            store.set_host_state("active_workspace", workspace)
            scenario = _MissionScenario()
            app, mcp = self._app(
                config=config,
                store=store,
                workspace=workspace,
                scenario=scenario,
            )
            with TestClient(app, base_url="http://localhost:8765") as client:
                _authenticate(client, config)
                bootstrap = client.get("/api/v1/bootstrap")
                self.assertEqual(bootstrap.status_code, 200, bootstrap.text)
                client.headers.update(
                    {"X-ControlStudio-CSRF": bootstrap.json()["csrfToken"]}
                )
                _wait_for_workspace_binding(mcp)
                _attach_workspace(client, workspace)
                conversation_id = self._create_conversation(client)
                _message, run, created = store.create_user_message_and_run(
                    conversation_id=conversation_id,
                    content="Verify the blocked read result.",
                    operation_id="seed-blocked-reverify",
                    workflow="engineering",
                    message_metadata={"workflow": "engineering"},
                    run_metadata={"workflow": "engineering"},
                )
                self.assertTrue(created)
                assert run is not None
                evidence_id = str(uuid.uuid4())
                store.append_event(
                    conversation_id=conversation_id,
                    run_id=run.id,
                    event_type="evidence.added",
                    payload={"evidenceId": evidence_id, "toolName": "xg5000.session_status"},
                )
                mission = _canonical_mission(
                    {
                        "schemaVersion": "1",
                        "objective": "Verify the blocked read result.",
                        "status": "blocked",
                        "completionCriteria": ["A read result is interpreted correctly."],
                        "steps": [
                            {
                                "stepId": "prior-read",
                                "title": "Read current target",
                                "status": "completed",
                                "outcome": "The target was read successfully.",
                                "evidenceRefs": [],
                            },
                            {
                                "stepId": "interpret-read",
                                "title": "Interpret current read",
                                "status": "blocked",
                                "outcome": "The prior successful read was treated as unavailable.",
                                "evidenceRefs": [evidence_id],
                            },
                        ],
                        "activeStepId": None,
                        "blockingQuestions": ["Can the successful read be rechecked?"],
                        "nextAction": "Wait for explicit user re-verification.",
                        "stopReason": "blocked_by_missing_capability",
                    }
                )
                mission_digest = _canonical_digest(mission)
                checkpoint = {
                    "schemaVersion": "1",
                    "cycle": 1,
                    "missionDigest": mission_digest,
                    "messages": [{"role": "assistant", "content": "checkpoint"}],
                }
                store.checkpoint_engineering_mission(
                    conversation_id=conversation_id,
                    run_id=run.id,
                    cycle=1,
                    cycle_disposition="respond",
                    engineering_mission=mission,
                    digest=mission_digest,
                    agent_checkpoint=checkpoint,
                    agent_checkpoint_digest=_canonical_digest(checkpoint),
                    final_message_content="The read interpretation is blocked pending explicit re-verification.",
                    final_message_metadata={"workflow": "engineering"},
                )
                store.update_run_state(run_id=run.id, state=RunState.COMPLETED)
                before = store.get_conversation(conversation_id)
                self.assertIsNotNone(before)
                assert before is not None
                response = client.post(
                    f"/api/v1/conversations/{conversation_id}/engineering-mission/reverify-blocked-step",
                    headers={"Idempotency-Key": "reverify-blocked-read"},
                    json={"operationId": "reverify-blocked-read"},
                )
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                self.assertEqual(payload["revision"], 2)
                self.assertEqual(payload["snapshot"]["status"], "active")
                self.assertEqual(payload["snapshot"]["activeStepId"], "interpret-read")
                selected = payload["snapshot"]["steps"][1]
                self.assertEqual(selected["status"], "active")
                self.assertIsNone(selected["outcome"])
                self.assertEqual(selected["evidenceRefs"], [evidence_id])
                self.assertEqual(payload["snapshot"]["steps"][0]["status"], "completed")
                self.assertEqual(scenario.runtime_calls, 0)
                after = store.get_conversation(conversation_id)
                self.assertIsNotNone(after)
                assert after is not None
                self.assertEqual(len(after.runs), len(before.runs))
                self.assertEqual(len(after.messages), len(before.messages))
                checkpoint_after = store.get_engineering_agent_checkpoint(conversation_id)
                self.assertIsNotNone(checkpoint_after)
                assert checkpoint_after is not None
                self.assertEqual(checkpoint_after.checkpoint["messages"], checkpoint["messages"])
                self.assertEqual(checkpoint_after.checkpoint["missionDigest"], payload["digest"])
                durable = store.get_engineering_mission(conversation_id)
                self.assertIsNotNone(durable)
                assert durable is not None
                canonical_snapshot, canonical_digest = canonical_engineering_mission_snapshot(
                    durable.engineering_mission
                )
                self.assertEqual(durable.engineering_mission, canonical_snapshot)
                self.assertEqual(durable.digest, canonical_digest)
                self.assertEqual(checkpoint_after.mission_digest, canonical_digest)
                self.assertEqual(
                    checkpoint_after.checkpoint["missionDigest"], canonical_digest
                )
                # Model the only persisted legacy defect the bounded startup
                # repair accepts: the reverify transition retained `outcome:
                # null` and hashed that non-canonical JSON representation.
                legacy_snapshot = json.loads(json.dumps(durable.engineering_mission))
                legacy_snapshot["steps"][1]["outcome"] = None
                legacy_digest = _canonical_digest(legacy_snapshot)
                legacy_checkpoint = json.loads(json.dumps(checkpoint_after.checkpoint))
                legacy_checkpoint["missionDigest"] = legacy_digest
                with sqlite3.connect(config.database_path) as connection:
                    connection.execute(
                        """
                        UPDATE engineering_missions
                        SET snapshot_json = ?, digest = ?
                        WHERE conversation_id = ?
                        """,
                        (
                            json.dumps(
                                legacy_snapshot,
                                ensure_ascii=False,
                                separators=(",", ":"),
                                sort_keys=True,
                            ),
                            legacy_digest,
                            conversation_id,
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE engineering_agent_checkpoints
                        SET mission_digest = ?, checkpoint_json = ?, digest = ?
                        WHERE conversation_id = ?
                        """,
                        (
                            legacy_digest,
                            json.dumps(
                                legacy_checkpoint,
                                ensure_ascii=False,
                                separators=(",", ":"),
                                sort_keys=True,
                            ),
                            _canonical_digest(legacy_checkpoint),
                            conversation_id,
                        ),
                    )
                    connection.commit()
                connection.close()
                self.assertEqual(
                    store.repair_buggy_reverified_engineering_mission_digests(), 1
                )
                repaired = store.get_engineering_mission(conversation_id)
                repaired_checkpoint = store.get_engineering_agent_checkpoint(conversation_id)
                self.assertIsNotNone(repaired)
                self.assertIsNotNone(repaired_checkpoint)
                assert repaired is not None and repaired_checkpoint is not None
                repaired_snapshot, repaired_digest = canonical_engineering_mission_snapshot(
                    repaired.engineering_mission
                )
                self.assertEqual(repaired.engineering_mission, repaired_snapshot)
                self.assertEqual(repaired.digest, repaired_digest)
                self.assertEqual(repaired_checkpoint.mission_digest, repaired_digest)
                self.assertEqual(
                    repaired_checkpoint.checkpoint["missionDigest"], repaired_digest
                )
                replay = client.post(
                    f"/api/v1/conversations/{conversation_id}/engineering-mission/reverify-blocked-step",
                    headers={"Idempotency-Key": "reverify-blocked-read"},
                    json={"operationId": "reverify-blocked-read"},
                )
                self.assertEqual(replay.status_code, 200, replay.text)
                self.assertEqual(replay.json()["revision"], 2)
                resumed = client.post(
                    f"/api/v1/conversations/{conversation_id}/engineering-mission/resume",
                    headers={"Idempotency-Key": "resume-reverified-blocked-read"},
                    json={"operationId": "resume-reverified-blocked-read"},
                )
                self.assertEqual(resumed.status_code, 202, resumed.text)
                resumed_run_id = str(resumed.json()["runId"])
                self.assertIsNotNone(store.get_run(resumed_run_id))
                # The fixture's Agent intentionally cannot author a valid
                # continuation plan, but the normal resume endpoint accepted
                # and created this run without the prior digest 409.
                self.assertEqual(
                    _wait_for_run(store, resumed_run_id).state,
                    RunState.FAILED,
                )

    def test_ready_ld_io_evidence_creates_only_a_pending_approval(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-ready-ld-proposal-") as temporary:
            root = Path(temporary)
            config = self._config(root)
            workspace = _workspace(root)
            store = HostStore(config.database_path)
            store.set_host_state("active_workspace", workspace)
            scenario = _ReadyLdProposalScenario()
            app, mcp = self._app(
                config=config,
                store=store,
                workspace=workspace,
                scenario=scenario,
            )

            with TestClient(app, base_url="http://localhost:8765") as client:
                _authenticate(client, config)
                _wait_for_workspace_binding(mcp)
                _attach_workspace(client, workspace)
                conversation_id = self._create_conversation(client)
                run_id = self._start_mission(
                    client,
                    conversation_id,
                    operation_id="ready-ld-proposal-only",
                )
                completed = _wait_for_run(store, run_id)

            self.assertEqual(
                completed.state,
                RunState.COMPLETED,
                f"{completed.error_code}: {completed.error_detail}",
            )
            self.assertIsNotNone(scenario.proposal)
            assert scenario.proposal is not None
            self.assertEqual(scenario.proposal["state"], "PENDING_APPROVAL")
            self.assertFalse(scenario.proposal["execution_permitted"])
            self.assertEqual(
                scenario.proposal["next_action"],
                "human_approval_required",
            )
            transactions = store.list_change_transactions(
                conversation_id=conversation_id
            )
            self.assertEqual(len(transactions), 1)
            self.assertEqual(
                transactions[0].state,
                ChangeTransactionState.PENDING_APPROVAL,
            )
            self.assertEqual(
                workspace["working_copy"]["current_sha256"],
                _WORKING_HASH,
            )

    def test_confirmed_internal_m_receipt_creates_pending_approval_without_hardware(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-ready-internal-m-") as temporary:
            root = Path(temporary)
            config = self._config(root)
            workspace = _workspace(root)
            store = HostStore(config.database_path)
            store.set_host_state("active_workspace", workspace)
            scenario = _ReadyLdProposalScenario(internal_m_without_hardware=True)
            app, mcp = self._app(
                config=config,
                store=store,
                workspace=workspace,
                scenario=scenario,
            )

            with TestClient(app, base_url="http://localhost:8765") as client:
                _authenticate(client, config)
                _wait_for_workspace_binding(mcp)
                _attach_workspace(client, workspace)
                conversation_id = self._create_conversation(client)
                run_id = self._start_mission(
                    client,
                    conversation_id,
                    operation_id="ready-internal-m-proposal-only",
                )
                completed = _wait_for_run(store, run_id)

            self.assertEqual(
                completed.state,
                RunState.COMPLETED,
                f"{completed.error_code}: {completed.error_detail}",
            )
            transactions = store.list_change_transactions(
                conversation_id=conversation_id
            )
            self.assertEqual(len(transactions), 1)
            self.assertEqual(transactions[0].state, ChangeTransactionState.PENDING_APPROVAL)
            self.assertEqual(transactions[0].plan["current"]["value"], "M0010")
            self.assertEqual(transactions[0].plan["proposed"]["value"], "M0011")
            self.assertIsNone(transactions[0].plan["target"]["hardwareContextBinding"])
            self.assertNotIn("hardwareEvidenceRef", transactions[0].plan["target"])

    def test_rejected_final_artifact_retains_same_run_host_proposal_truthfully(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-ready-ld-final-artifact-") as temporary:
            root = Path(temporary)
            config = self._config(root)
            workspace = _workspace(root)
            store = HostStore(config.database_path)
            store.set_host_state("active_workspace", workspace)
            scenario = _ReadyLdProposalScenario(reject_final_artifact=True)
            app, mcp = self._app(
                config=config,
                store=store,
                workspace=workspace,
                scenario=scenario,
            )

            with TestClient(app, base_url="http://localhost:8765") as client:
                _authenticate(client, config)
                _wait_for_workspace_binding(mcp)
                _attach_workspace(client, workspace)
                conversation_id = self._create_conversation(client)
                run_id = self._start_mission(
                    client,
                    conversation_id,
                    operation_id="ready-ld-retained-proposal-final-artifact",
                )
                failed = _wait_for_run(store, run_id)

            self.assertEqual(failed.state, RunState.FAILED)
            self.assertEqual(failed.error_code, "agent_output_contract")
            self.assertEqual(
                failed.error_detail,
                "Final response processing failed the structured evidence/safety "
                "contract after the Host created an approval-pending change contract "
                "for this run; the retained contract was not invalidated. No execution "
                "or mutation outcome is implied.",
            )
            self.assertIsNotNone(scenario.proposal)
            assert scenario.proposal is not None
            self.assertEqual(scenario.proposal["state"], "PENDING_APPROVAL")
            transactions = store.list_change_transactions(
                conversation_id=conversation_id
            )
            self.assertEqual(len(transactions), 1)
            self.assertEqual(
                transactions[0].state,
                ChangeTransactionState.PENDING_APPROVAL,
            )

    def test_engineering_analysis_output_failure_keeps_tool_history_wording(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-engineering-analysis-output-") as temporary:
            root = Path(temporary)
            config = self._config(root)
            workspace = _workspace(root)
            store = HostStore(config.database_path)
            store.set_host_state("active_workspace", workspace)
            scenario = _MissionScenario(fail_engineering_analysis_output=True)
            app, mcp = self._app(
                config=config,
                store=store,
                workspace=workspace,
                scenario=scenario,
            )

            with TestClient(app, base_url="http://localhost:8765") as client:
                _authenticate(client, config)
                _wait_for_workspace_binding(mcp)
                _attach_workspace(client, workspace)
                conversation_id = self._create_conversation(client)
                run_id = self._start_mission(
                    client,
                    conversation_id,
                    operation_id="engineering-analysis-output-failure",
                )
                failed = _wait_for_run(store, run_id)

            self.assertEqual(failed.state, RunState.FAILED)
            self.assertEqual(failed.error_code, "agent_output_contract")
            conversation = store.get_conversation(conversation_id)
            self.assertIsNotNone(conversation)
            assert conversation is not None
            self.assertEqual(
                conversation.messages[-1].content,
                "분석 응답을 완료하지 못했습니다. 확인된 도구 실행 결과는 "
                "실행 기록에 남아 있습니다.",
            )

    def test_ready_ld_proposal_rejects_retained_hardware_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-ld-hash-mismatch-") as temporary:
            root = Path(temporary)
            config = self._config(root)
            workspace = _workspace(root)
            store = HostStore(config.database_path)
            store.set_host_state("active_workspace", workspace)
            scenario = _ReadyLdProposalScenario(mismatched_host_owned_hash=True)
            app, mcp = self._app(
                config=config,
                store=store,
                workspace=workspace,
                scenario=scenario,
            )

            with TestClient(app, base_url="http://localhost:8765") as client:
                _authenticate(client, config)
                _wait_for_workspace_binding(mcp)
                _attach_workspace(client, workspace)
                conversation_id = self._create_conversation(client)
                run_id = self._start_mission(
                    client,
                    conversation_id,
                    operation_id="reject-ready-ld-host-hash-mismatch",
                )
                failed = _wait_for_run(store, run_id)

            self.assertEqual(failed.state, RunState.FAILED)
            self.assertEqual(failed.error_code, "runtime_precondition")
            self.assertIn("unobserved required hardware context", failed.error_detail or "")
            self.assertEqual(
                store.list_change_transactions(conversation_id=conversation_id),
                [],
            )

    def test_missing_mne_bundle_keeps_offline_candidate_planner_available(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-mission-mne-planning-") as temporary:
            root = Path(temporary)
            config = self._config(root)
            workspace = _workspace(root)
            store = HostStore(config.database_path)
            store.set_host_state("active_workspace", workspace)
            scenario = _MissionScenario(capture_mne_planner_only=True)
            app, mcp = self._app(
                config=config,
                store=store,
                workspace=workspace,
                scenario=scenario,
            )

            with TestClient(app, base_url="http://localhost:8765") as client:
                _authenticate(client, config)
                _wait_for_workspace_binding(mcp)
                _attach_workspace(client, workspace)
                conversation_id = self._create_conversation(client)
                run_id = self._start_mission(
                    client,
                    conversation_id,
                    operation_id="mne-planner-without-executor",
                )
                completed = _wait_for_run(store, run_id)

                self.assertEqual(completed.state, RunState.COMPLETED)
                self.assertEqual(scenario.mne_planner_available, [True])
                self.assertEqual(mcp.mne_capability_status(), "missing")

    def test_same_run_proposal_rejects_a_non_approval_wait_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-mission-invariant-") as temporary:
            root = Path(temporary)
            config = self._config(root)
            workspace = _workspace(root)
            store = HostStore(config.database_path)
            store.set_host_state("active_workspace", workspace)
            scenario = _MissionScenario(invalid_after_proposal=True)
            app, mcp = self._app(
                config=config,
                store=store,
                workspace=workspace,
                scenario=scenario,
            )

            with TestClient(app, base_url="http://localhost:8765") as client:
                _authenticate(client, config)
                _wait_for_workspace_binding(mcp)
                _attach_workspace(client, workspace)
                conversation_id = self._create_conversation(client)
                run_id = self._start_mission(
                    client,
                    conversation_id,
                    operation_id="invalid-same-run-proposal",
                )
                failed = _wait_for_run(store, run_id)

                self.assertEqual(failed.state, RunState.FAILED)
                self.assertEqual(failed.error_code, "agent_output_contract")
                self.assertEqual(
                    failed.error_detail,
                    "Model output failed the structured evidence/safety contract; "
                    "no model-authored plan or mutation was accepted. "
                    "Safe diagnostic: stage=mission_checkpoint; "
                    "exceptionKind=host_validation; "
                    "reasonCode=progression_rejected; "
                    "validatorCode=approval_state; "
                    "validationErrors=none.",
                )
                self.assertEqual(
                    scenario.contract_errors,
                    [
                        (
                            "AgentOutputContractError: a live change approval requires "
                            "an approval-waiting mission"
                        )
                    ],
                )
                self.assertIsNone(store.get_engineering_mission(conversation_id))
                self.assertEqual(
                    len(store.list_change_transactions(conversation_id=conversation_id)),
                    1,
                )

                replay = client.post(
                    f"/api/v1/conversations/{conversation_id}/messages",
                    headers={"Idempotency-Key": "invalid-same-run-proposal"},
                    json={
                        "content": "Create one reviewed offline change and verify its outcome.",
                        "operationId": "invalid-same-run-proposal",
                        "changeRequestScope": "offline_candidate_revision",
                    },
                )
                self.assertEqual(replay.status_code, 202, replay.text)
                self.assertEqual(replay.json()["runId"], run_id)
                self.assertEqual(scenario.runtime_calls, 1)
                self.assertEqual(
                    len(store.list_change_transactions(conversation_id=conversation_id)),
                    1,
                )

    def test_same_run_store_transaction_cannot_forge_callback_authority(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-mission-forged-") as temporary:
            root = Path(temporary)
            config = self._config(root)
            workspace = _workspace(root)
            store = HostStore(config.database_path)
            store.set_host_state("active_workspace", workspace)
            scenario = _MissionScenario()
            app, mcp = self._app(
                config=config,
                store=store,
                workspace=workspace,
                scenario=scenario,
            )

            with TestClient(app, base_url="http://localhost:8765") as client:
                _authenticate(client, config)
                _wait_for_workspace_binding(mcp)
                _attach_workspace(client, workspace)
                conversation_id = self._create_conversation(client)
                run_id = self._start_mission(
                    client,
                    conversation_id,
                    operation_id="forged-same-run-authority",
                )
                failed = _wait_for_run(store, run_id)

                self.assertEqual(failed.state, RunState.FAILED)
                self.assertEqual(failed.error_code, "agent_output_contract")
                self.assertEqual(
                    failed.error_detail,
                    "Model output failed the structured evidence/safety contract; "
                    "no model-authored plan or mutation was accepted. "
                    "Safe diagnostic: stage=mission_checkpoint; "
                    "exceptionKind=host_validation; "
                    "reasonCode=progression_rejected; "
                    "validatorCode=approval_authority_mismatch; "
                    "validationErrors=none.",
                )
                self.assertEqual(
                    scenario.contract_errors,
                    [
                        (
                            "AgentOutputContractError: approval-waiting mission differs "
                            "from the exact Host change authority for this run"
                        )
                    ],
                )
                self.assertIsNone(store.get_engineering_mission(conversation_id))
                self.assertEqual(
                    len(store.list_change_transactions(conversation_id=conversation_id)),
                    1,
                )

    def test_v20_waiting_mission_backfills_unique_target_after_followup_run(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-mission-migration-") as temporary:
            root = Path(temporary)
            config = self._config(root)
            workspace = _workspace(root)
            scenario = _MissionScenario()
            store = HostStore(config.database_path)
            store.set_host_state("active_workspace", workspace)
            app, mcp = self._app(
                config=config,
                store=store,
                workspace=workspace,
                scenario=scenario,
            )

            with TestClient(app, base_url="http://localhost:8765") as client:
                _authenticate(client, config)
                _wait_for_workspace_binding(mcp)
                _attach_workspace(client, workspace)
                conversation_id = self._create_conversation(client)
                run_id = self._seed_waiting_mission(
                    store=store,
                    scenario=scenario,
                    conversation_id=conversation_id,
                    operation_id="legacy-waiting-mission",
                )
                completed = store.get_run(run_id)
                self.assertIsNotNone(completed)
                assert completed is not None
                self.assertEqual(completed.state, RunState.COMPLETED)
                self.assertIsNotNone(scenario.transaction_id)

            _message, followup_run, created = store.create_user_message_and_run(
                conversation_id=conversation_id,
                content="Re-check the still-pending approval without replacing it.",
                operation_id="legacy-followup-waiting-run",
                workflow="engineering",
                message_metadata={"workflow": "engineering"},
                run_metadata={"workflow": "engineering"},
            )
            self.assertTrue(created)
            self.assertIsNotNone(followup_run)
            assert followup_run is not None
            store.update_run_state(
                run_id=followup_run.id,
                state=RunState.COMPLETED,
            )

            with sqlite3.connect(config.database_path) as connection:
                connection.execute(
                    """
                    UPDATE engineering_missions
                    SET updated_by_run_id = ?, change_transaction_id = NULL
                    WHERE conversation_id = ?
                    """,
                    (followup_run.id, conversation_id),
                )
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version = 21"
                )
                connection.commit()
            connection.close()

            migrated_store = HostStore(config.database_path)
            migrated = migrated_store.get_engineering_mission(conversation_id)
            self.assertIsNotNone(migrated)
            assert migrated is not None
            self.assertEqual(
                migrated.change_transaction_id,
                scenario.transaction_id,
            )

    def test_terminal_change_resumes_waiting_mission_once_with_outcome_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-mission-outcome-") as temporary:
            root = Path(temporary)
            config = self._config(root)
            workspace = _workspace(root)
            scenario = _MissionScenario(terminal_failures_before_checkpoint=1)
            first_store = HostStore(config.database_path)
            first_store.set_host_state("active_workspace", workspace)
            first_app, first_mcp = self._app(
                config=config,
                store=first_store,
                workspace=workspace,
                scenario=scenario,
            )

            with TestClient(first_app, base_url="http://localhost:8765") as client:
                _authenticate(client, config)
                _wait_for_workspace_binding(first_mcp)
                _attach_workspace(client, workspace)
                conversation_id = self._create_conversation(client)
                initial_run_id = self._seed_waiting_mission(
                    store=first_store,
                    scenario=scenario,
                    conversation_id=conversation_id,
                    operation_id="create-change-mission",
                )
                completed = first_store.get_run(initial_run_id)
                self.assertIsNotNone(completed)
                assert completed is not None
                self.assertEqual(
                    completed.state,
                    RunState.COMPLETED,
                    f"{completed.error_detail}; {scenario.contract_errors}",
                )
                mission = first_store.get_engineering_mission(conversation_id)
                self.assertIsNotNone(mission)
                assert mission is not None
                self.assertEqual(
                    mission.engineering_mission["status"],
                    "waiting_for_approval",
                )
                self.assertIsNotNone(scenario.transaction_id)
                self.assertEqual(
                    getattr(mission, "change_transaction_id", None),
                    scenario.transaction_id,
                )

                self._assert_resume_blocked(
                    client,
                    first_store,
                    conversation_id,
                    ChangeTransactionState.PENDING_APPROVAL,
                )

                assert scenario.transaction_id is not None
                transaction = first_store.get_change_transaction(scenario.transaction_id)
                self.assertIsNotNone(transaction)
                assert transaction is not None
                approved, recorded = first_store.decide_change_transaction(
                    transaction_id=transaction.id,
                    decision=ApprovalDecision.APPROVE,
                    expected_plan_digest=transaction.plan_digest,
                    actor_id="mission-fixture-operator",
                )
                self.assertTrue(recorded)
                self.assertIsNotNone(approved)
                self._assert_resume_blocked(
                    client,
                    first_store,
                    conversation_id,
                    ChangeTransactionState.APPROVED,
                )

                executing = first_store.begin_change_execution(
                    transaction_id=transaction.id,
                    expected_plan_digest=transaction.plan_digest,
                )
                self.assertIsNotNone(executing)
                self._assert_resume_blocked(
                    client,
                    first_store,
                    conversation_id,
                    ChangeTransactionState.EXECUTING,
                )

                verifying = first_store.mark_change_verifying(
                    transaction_id=transaction.id,
                    apply_evidence={"readback": "applied-once"},
                )
                self.assertIsNotNone(verifying)
                self._assert_resume_blocked(
                    client,
                    first_store,
                    conversation_id,
                    ChangeTransactionState.VERIFYING,
                )

                observing = first_store.finish_change_execution(
                    transaction_id=transaction.id,
                    state=ChangeTransactionState.OBSERVING,
                    verification_evidence={
                        "nativeDiff": "verified",
                        "simulation_receipt": {"receipt_digest": "d" * 64},
                    },
                )
                self.assertIsNotNone(observing)
                self._assert_resume_blocked(
                    client,
                    first_store,
                    conversation_id,
                    ChangeTransactionState.OBSERVING,
                )

                self.assertEqual(
                    first_store.reconcile_interrupted_change_transactions(),
                    1,
                )
                quarantined = first_store.get_change_transaction(transaction.id)
                self.assertIsNotNone(quarantined)
                assert quarantined is not None
                self.assertEqual(
                    quarantined.state,
                    ChangeTransactionState.QUARANTINED,
                )
                self._assert_resume_blocked(
                    client,
                    first_store,
                    conversation_id,
                    ChangeTransactionState.QUARANTINED,
                )

                finished = first_store.resolve_quarantined_change(
                    transaction_id=transaction.id,
                    state=ChangeTransactionState.COMPLETED,
                    evidence={"resolution": "verified-after-readback"},
                    actor_id="mission-fixture-operator",
                )
                self.assertIsNotNone(finished)
                assert finished is not None

            # A fresh app and Store prove that continuation authority comes from
            # durable mission/transaction state rather than closure-local memory.
            second_store = HostStore(config.database_path)
            second_app, second_mcp = self._app(
                config=config,
                store=second_store,
                workspace=workspace,
                scenario=scenario,
            )
            with TestClient(second_app, base_url="http://localhost:8765") as client:
                _authenticate(client, config)
                _wait_for_workspace_binding(second_mcp)
                _attach_workspace(client, workspace)
                failed_response = client.post(
                    f"/api/v1/conversations/{conversation_id}/engineering-mission/resume",
                    headers={"Idempotency-Key": "resume-terminal-change-fail"},
                    json={"operationId": "resume-terminal-change-fail"},
                )
                self.assertEqual(failed_response.status_code, 202, failed_response.text)
                failed_run = _wait_for_run(
                    second_store,
                    str(failed_response.json()["runId"]),
                )
                self.assertEqual(failed_run.state, RunState.FAILED)
                self.assertEqual(scenario.runtime_calls, 1)
                self.assertEqual(scenario.outcome_evidence_seen_before_runtime, [False])
                failed_outcome_id = scenario.change_outcomes[0]["evidenceId"]
                self.assertFalse(
                    any(
                        event.payload.get("evidenceId") == failed_outcome_id
                        for event in second_store.list_events(
                            conversation_id=conversation_id,
                            limit=250,
                        )
                    )
                )

                response = client.post(
                    f"/api/v1/conversations/{conversation_id}/engineering-mission/resume",
                    headers={"Idempotency-Key": "resume-terminal-change"},
                    json={"operationId": "resume-terminal-change"},
                )
                self.assertEqual(response.status_code, 202, response.text)
                continuation_run_id = str(response.json()["runId"])
                continued = _wait_for_run(second_store, continuation_run_id)
                self.assertEqual(continued.state, RunState.COMPLETED)

                self.assertEqual(scenario.runtime_calls, 2)
                self.assertEqual(
                    scenario.outcome_evidence_seen_before_runtime,
                    [False, False],
                )
                self.assertEqual(len(scenario.change_outcomes), 2)
                self.assertEqual(scenario.change_outcomes[0], scenario.change_outcomes[1])
                outcome = scenario.change_outcomes[1]
                unsigned_outcome = {
                    "schemaVersion": "1",
                    "transactionId": transaction.id,
                    "planDigest": transaction.plan_digest,
                    "state": "COMPLETED",
                    "disposition": "applied",
                    "verificationEvidenceDigest": _canonical_digest(finished.result),
                    "errorCode": None,
                }
                expected_outcome_digest = _canonical_digest(unsigned_outcome)
                expected_evidence_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"controlstudio:change-outcome:{transaction.id}:{expected_outcome_digest}",
                    )
                )
                self.assertEqual(
                    outcome,
                    {
                        **unsigned_outcome,
                        "evidenceId": expected_evidence_id,
                        "outcomeDigest": expected_outcome_digest,
                    },
                )
                self.assertEqual(set(outcome), _CHANGE_OUTCOME_KEYS)

                evidence = second_store.list_run_evidence(
                    conversation_id=conversation_id,
                    run_id=continuation_run_id,
                )
                matching = [
                    item
                    for item in evidence
                    if item.payload.get("evidenceId") == outcome["evidenceId"]
                ]
                self.assertEqual(len(matching), 1)
                self.assertEqual(
                    len(
                        [
                            event
                            for event in second_store.list_events(
                                conversation_id=conversation_id,
                                limit=250,
                            )
                            if event.event_type == "evidence.added"
                            and event.payload.get("evidenceId") == outcome["evidenceId"]
                        ]
                    ),
                    1,
                )
                receipt = matching[0].payload.get("receipt")
                self.assertIsInstance(receipt, dict)
                assert isinstance(receipt, dict)
                self.assertEqual(
                    {key: receipt.get(key) for key in _CHANGE_OUTCOME_KEYS},
                    outcome,
                )
                receipt_core = dict(receipt)
                receipt_digest = receipt_core.pop("receiptDigest", None)
                self.assertEqual(receipt_digest, _canonical_digest(receipt_core))

                durable = second_store.get_engineering_mission(conversation_id)
                self.assertIsNotNone(durable)
                assert durable is not None
                self.assertEqual(durable.engineering_mission["status"], "completed")
                self.assertIn(
                    outcome["evidenceId"],
                    durable.engineering_mission["steps"][1]["evidenceRefs"],
                )
                fresh_target_evidence = [
                    item.payload
                    for item in evidence
                    if item.payload.get("toolName") == "xg5000.session_status"
                ]
                self.assertEqual(len(fresh_target_evidence), 1)
                self.assertIn(
                    fresh_target_evidence[0]["evidenceId"],
                    durable.engineering_mission["steps"][1]["evidenceRefs"],
                )

                replay = client.post(
                    f"/api/v1/conversations/{conversation_id}/engineering-mission/resume",
                    headers={"Idempotency-Key": "resume-terminal-change"},
                    json={"operationId": "resume-terminal-change"},
                )
                self.assertEqual(replay.status_code, 202, replay.text)
                self.assertEqual(replay.json()["runId"], continuation_run_id)
                self.assertEqual(scenario.runtime_calls, 2)
                self.assertEqual(
                    len(second_store.list_change_transactions(conversation_id=conversation_id)),
                    1,
                )
                detail = second_store.get_conversation(conversation_id)
                self.assertIsNotNone(detail)
                assert detail is not None
                self.assertEqual(len(detail.runs), 3)
                self.assertEqual(len(detail.messages), 6)
                continuation_message = next(
                    message
                    for message in detail.messages
                    if message.id == continued.request_message_id
                )
                self.assertEqual(continuation_message.role, MessageRole.SYSTEM)
                self.assertTrue(continuation_message.metadata.get("engineering_mission_continue"))
                self.assertEqual(
                    continuation_message.metadata.get("change_outcome_transaction_id"),
                    transaction.id,
                )
                self.assertEqual(
                    continued.metadata.get("change_outcome_transaction_id"),
                    transaction.id,
                )

    def test_restart_rehydrates_mission_evidence_without_repeating_the_tool(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="controlstudio-historical-evidence-"
        ) as temporary:
            root = Path(temporary)
            config = self._config(root)
            workspace = _workspace(root)
            first_store = HostStore(config.database_path)
            first_store.set_host_state("active_workspace", workspace)
            scenario = _HistoricalEvidenceScenario()
            first_app, first_mcp = self._app(
                config=config,
                store=first_store,
                workspace=workspace,
                scenario=scenario,
            )
            with TestClient(first_app, base_url="http://localhost:8765") as client:
                _authenticate(client, config)
                _wait_for_workspace_binding(first_mcp)
                _attach_workspace(client, workspace)
                conversation_id = self._create_conversation(client)
                first_run_id = self._start_mission(
                    client,
                    conversation_id,
                    operation_id="historical-evidence-first-run",
                )
                first_run = _wait_for_run(first_store, first_run_id)
                self.assertEqual(first_run.state, RunState.COMPLETED)

            second_store = HostStore(config.database_path)
            second_app, second_mcp = self._app(
                config=config,
                store=second_store,
                workspace=workspace,
                scenario=scenario,
            )
            with TestClient(second_app, base_url="http://localhost:8765") as client:
                _authenticate(client, config)
                _wait_for_workspace_binding(second_mcp)
                _attach_workspace(client, workspace)
                second_run_id = self._start_mission(
                    client,
                    conversation_id,
                    operation_id="historical-evidence-second-run",
                )
                second_run = _wait_for_run(second_store, second_run_id)
                self.assertEqual(second_run.state, RunState.COMPLETED)

            self.assertEqual(scenario.runtime_calls, 2)
            self.assertEqual(scenario.tool_completions, 1)
            self.assertTrue(scenario.resumed_checkpoint_seen)
            self.assertIsNotNone(scenario.first_evidence)
            self.assertIsNotNone(scenario.resumed_bundle)
            assert scenario.first_evidence is not None
            assert scenario.resumed_bundle is not None
            self.assertEqual(
                scenario.resumed_bundle["missionDigest"],
                scenario.first_mission_digest,
            )
            self.assertEqual(
                scenario.resumed_bundle["items"],
                [
                    {
                        "sourceRunId": first_run_id,
                        "evidence": scenario.first_evidence,
                    }
                ],
            )
            unsigned_bundle = {
                key: value
                for key, value in scenario.resumed_bundle.items()
                if key != "bundleDigest"
            }
            self.assertEqual(
                scenario.resumed_bundle["bundleDigest"],
                _canonical_digest(unsigned_bundle),
            )
            self.assertEqual(
                second_store.list_run_evidence(
                    conversation_id=conversation_id,
                    run_id=second_run_id,
                ),
                [],
            )
            second_run_tool_events = [
                event
                for event in second_store.list_events(
                    conversation_id=conversation_id,
                    limit=1_000,
                )
                if event.run_id == second_run_id
                and event.event_type
                in {"tool.started", "tool.completed", "tool.failed"}
            ]
            self.assertEqual(second_run_tool_events, [])


if __name__ == "__main__":
    unittest.main()
