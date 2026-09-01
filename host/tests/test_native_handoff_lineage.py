from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from controlstudio_host import native_projects
from controlstudio_host.agent_runtime import AgentRunOutput
from controlstudio_host.api_models import (
    EngineeringConversationHandoffV1,
    EngineeringMissionSnapshotV1,
)
from controlstudio_host.app import create_app
from controlstudio_host.config import HostConfig
from controlstudio_host.mcp_client import MCPStatus
from controlstudio_host.model_connection import ModelConnectionRepository
from controlstudio_host.models import RunState
from controlstudio_host.native_projects import (
    NATIVE_BUILD_BUNDLE_PHASE,
    NATIVE_BUILD_BUNDLE_RECEIPT_SCHEMA,
    NATIVE_BUILD_BUNDLE_SCHEMA,
    NATIVE_SIMULATION_RECEIPT_SCHEMA,
    NATIVE_SIMULATION_TRIAL_SCHEMA,
    NativeProjectOperation,
    NativeProjectRepository,
    _native_simulation_trial_id,
    _new_build_workspace_provenance,
    validated_new_build_workspace_provenance,
)
from controlstudio_host.store import HostStore


def _digest(value: dict[str, object]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _completed_operation(root: Path) -> NativeProjectOperation:
    operation_id = "11111111-1111-4111-8111-111111111111"
    plan_digest = "1" * 64
    project_hash = "2" * 64
    reviewed_plan = {
        "schemaVersion": "2",
        "conversationId": "planning-conversation",
        "messageId": "planning-message",
        "planDigest": "3" * 64,
        "projectBriefDigest": "4" * 64,
        "attachmentRevisionDigest": "5" * 64,
        "reviewedSectionIds": ["overview"],
        "reviewedItemIds": ["overview:objective"],
    }
    bundle_payload: dict[str, object] = {
        "schemaVersion": NATIVE_BUILD_BUNDLE_SCHEMA,
        "phase": NATIVE_BUILD_BUNDLE_PHASE,
        "reviewedPlan": reviewed_plan,
        "engineeringPlan": {"schemaVersion": "2", "status": "ready_for_review"},
        "technicalConfiguration": {
            "projectName": "generated",
            "plcSeries": "XGK",
            "cpuModel": "XGK-CPUE",
            "programName": "MAIN",
            "language": "LD",
            "description": "lineage fixture",
        },
        "compiledProgram": {
            "source": "OUT M000",
            "rungIds": ["rung-1"],
            "sourceSha256": "6" * 64,
            "ladderIrSha256": "7" * 64,
        },
    }
    bundle_id = _digest(bundle_payload)
    build_bundle = {**bundle_payload, "bundleId": bundle_id}
    destination = (root / "generated-native").resolve()
    project_path = destination / "generated.xgwx"
    request = {
        "operationId": operation_id,
        "projectName": "generated",
        "workspaceParent": str(root),
        "destinationDirectory": str(destination),
        "plcSeries": "XGK",
        "cpuModel": "XGK-CPUE",
        "programName": "MAIN",
        "language": "LD",
        "description": "lineage fixture",
        "xg5000Pid": 41,
        "xg5000Hwnd": 42,
        "reviewedPlan": reviewed_plan,
        "buildBundle": build_bundle,
    }
    effective_description = (
        f"lineage fixture\n[GIDONG plan={reviewed_plan['planDigest'][:12]} bundle={bundle_id[:12]}]"
    )
    build_receipt = {
        "schemaVersion": NATIVE_BUILD_BUNDLE_RECEIPT_SCHEMA,
        "bundleId": bundle_id,
        "phase": NATIVE_BUILD_BUNDLE_PHASE,
        "status": "APPLIED_VERIFIED",
        "reviewedPlanDigest": reviewed_plan["planDigest"],
        "nativeReadback": {
            "projectName": "generated",
            "projectPath": str(project_path),
            "projectSha256": project_hash,
            "projectSize": 128,
            "destinationDirectory": str(destination),
            "plcSeries": "XGK",
            "cpuModel": "XGK-CPUE",
            "programName": "MAIN",
            "language": "LD",
            "description": effective_description,
            "identityVerified": True,
        },
        "programReadback": {
            "rungCount": 1,
            "rungIds": ["rung-1"],
            "ladderIrSha256": "7" * 64,
            "sourceSha256": "6" * 64,
            "exactInstructionReadback": ["OUT M000"],
            "finalView": "LD",
            "programCheckPassed": True,
            "savedProjectSha256": project_hash,
        },
    }
    provenance = _new_build_workspace_provenance(
        native_project_id=operation_id,
        build_bundle=build_bundle,
        build_receipt=build_receipt,
        project_sha256=project_hash,
    )
    workspace = {
        "ok": True,
        "verified": True,
        "workspace_id": "22222222-2222-4222-8222-222222222222",
        "revision_id": "33333333-3333-4333-8333-333333333333",
        "source_kind": "new_build",
        "vendor": "ls",
        "engineering_system": "XG5000",
        "created_at": datetime(2026, 8, 16, tzinfo=timezone.utc).isoformat(),
        "source": {
            "original_path": str(project_path),
            "captured_path": str(root / "captured.xgwx"),
            "sha256": project_hash,
            "original_state": "unchanged",
            "original_current_sha256": project_hash,
        },
        "working_copy": {
            "path": str(root / "working.xgwx"),
            "baseline_sha256": project_hash,
            "current_sha256": project_hash,
            "state": "clean",
        },
        "provenance": provenance,
    }
    simulation_receipt = {
        "schemaVersion": NATIVE_SIMULATION_RECEIPT_SCHEMA,
        "status": "SIMULATION_VERIFIED",
        "controllerConnected": False,
        "plcWritePerformed": False,
        "simulator": {"cpuStopped": True},
        "inputWriteBoundary": {
            "baselineRestoredAfterCpuStop": True,
            "restore": {"attempted": True, "matched": True},
        },
        "scenarioReceipt": {
            "schemaVersion": "controlstudio-native-simulation-scenarios/v3",
            "status": "SCENARIOS_VERIFIED",
        },
    }
    simulation_digest = _digest(simulation_receipt)
    trial_id = _native_simulation_trial_id(operation_id, bundle_id)
    result = {
        "identityVerified": True,
        "operation": "project.create_native",
        "xg5000Pid": 41,
        "xg5000Hwnd": 42,
        "projectPath": str(project_path),
        "projectSha256": project_hash,
        "projectSize": 128,
        "destinationDirectory": str(destination),
        "planDigest": plan_digest,
        "buildBundleId": bundle_id,
        "reviewedPlanDigest": reviewed_plan["planDigest"],
        "buildBundleReceipt": build_receipt,
        "controllerConnected": False,
        "plcWritePerformed": False,
        "engineeredProgramReady": True,
        "workspaceBound": True,
        "workspaceRegistration": {
            "state": "captured_and_bound",
            "workspace": workspace,
        },
        "simulationVerification": {
            "state": "verified",
            "receipt": simulation_receipt,
            "trial": {
                "schemaVersion": NATIVE_SIMULATION_TRIAL_SCHEMA,
                "trialId": trial_id,
                "state": "ACCEPTED",
                "safeRestorationVerified": True,
                "receiptDigest": simulation_digest,
                "decision": {
                    "outcome": "ACCEPTED",
                    "actorId": "operator",
                    "decidedAt": "2026-08-16T00:00:00+00:00",
                    "receiptDigest": simulation_digest,
                },
            },
        },
    }
    timestamp = datetime(2026, 8, 16, tzinfo=timezone.utc)
    return NativeProjectOperation(
        id=operation_id,
        operation_id=operation_id,
        state="COMPLETED",
        request_digest="8" * 64,
        actor_id="operator",
        request=request,
        plan_digest=plan_digest,
        destination_directory=str(destination),
        result=result,
        error_code=None,
        error_detail=None,
        created_at=timestamp,
        updated_at=timestamp,
        completed_at=timestamp,
    )


def _insert(repository: NativeProjectRepository, operation: NativeProjectOperation) -> None:
    timestamp = operation.created_at.isoformat()
    with repository._connection() as connection, connection:
        connection.execute(
            """
            INSERT INTO native_project_operations(
                id, operation_id, state, request_digest, actor_id,
                request_json, plan_digest, destination_directory,
                result_json, error_code, error_detail,
                created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
            """,
            (
                operation.id,
                operation.operation_id,
                operation.state,
                operation.request_digest,
                operation.actor_id,
                json.dumps(operation.request, separators=(",", ":"), sort_keys=True),
                operation.plan_digest,
                operation.destination_directory,
                json.dumps(operation.result, separators=(",", ":"), sort_keys=True),
                timestamp,
                timestamp,
                timestamp,
            ),
        )


class _NativeHandoffMCP:
    def __init__(self, workspace: dict[str, object]) -> None:
        self.workspace = workspace
        self.status = MCPStatus(
            status="ready",
            server_name="native-handoff-fixture-mcp",
            server_version="1.0.0",
            protocol_version="2025-11-25",
            tool_count=80,
            agent_tool_count=4,
        )
        self._identity: dict[str, object] | None = None

    @property
    def agent_session_identity_snapshot(self) -> dict[str, object] | None:
        return dict(self._identity) if self._identity is not None else None

    async def start(self) -> MCPStatus:
        return self.status

    async def stop(self) -> None:
        return None

    def bind_agent_workspace(self, workspace: dict[str, object]) -> None:
        self._identity = dict(workspace)

    def bind_agent_session(self, session: dict[str, object]) -> None:
        self._identity = dict(session)

    def clear_agent_session(self) -> None:
        self._identity = None

    def clear_controller_endpoint(self) -> None:
        return None

    async def call_workspace_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        if name != "workspace.status" or arguments != {
            "workspace_id": self.workspace["workspace_id"]
        }:
            raise AssertionError(f"unexpected workspace call: {name} {arguments}")
        return {"ok": True, "verified": True, "operation": name, **self.workspace}

    async def call_read_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        if name != "xg5000.attach":
            raise AssertionError(f"unexpected read call: {name} {arguments}")
        working_copy = self.workspace["working_copy"]
        if not isinstance(working_copy, dict):
            raise TypeError("fixture working copy is invalid")
        return {
            "schema_version": "1",
            "session_id": "native-handoff-session",
            "pid": int(arguments["pid"]),
            "hwnd": int(arguments["hwnd"]),
            "stage": "ANALYSIS_READY",
            "project_path": working_copy["path"],
            "project_hash": working_copy["current_sha256"],
            "metadata": {
                "project_identity_verified_against_selected_pid": True,
                "project_identity_read_only_bound_to_selected_pid": True,
                "project_identity_assurance": "strong_readback",
                "project_identity_proof": {
                    "pid": int(arguments["pid"]),
                    "main_hwnd": int(arguments["hwnd"]),
                    "process_create_time": 123.0,
                    "project_path": working_copy["path"],
                    "project_hash": working_copy["current_sha256"],
                },
            },
        }

    def mne_capability_status(self) -> str:
        return "missing"


class _NativeHandoffRuntime:
    def __init__(self, store: HostStore) -> None:
        self.store = store
        self.calls: list[dict[str, object]] = []
        self.evidence_before_checkpoint: list[object] = []

    async def run(self, **kwargs: object) -> AgentRunOutput:
        trusted_context = kwargs["trusted_context"]
        if not isinstance(trusted_context, dict):
            raise TypeError("trusted context is missing")
        status = trusted_context.get("native_engineering_status")
        if not isinstance(status, dict):
            raise TypeError("native engineering status was not propagated")
        self.calls.append(copy.deepcopy(status))
        conversation_id = str(kwargs["conversation_id"])
        run_id = str(kwargs["run_id"])
        self.evidence_before_checkpoint = list(
            self.store.list_run_evidence(
                conversation_id=conversation_id,
                run_id=run_id,
            )
        )
        mission = EngineeringMissionSnapshotV1.model_validate(
            {
                "schemaVersion": "1",
                "objective": status["objective"],
                "status": "waiting_for_user",
                "completionCriteria": status["completionCriteria"],
                "steps": [
                    {
                        "stepId": "engineering-validation",
                        "title": "Gather current engineering validation evidence",
                        "status": "pending",
                        "outcome": None,
                        "evidenceRefs": [],
                    }
                ],
                "activeStepId": None,
                "blockingQuestions": ["Run the current-target validation reads?"],
                "nextAction": None,
                "stopReason": "needs_user_input",
            }
        ).model_dump(mode="json", by_alias=True, exclude_none=True)
        mission_digest = _digest(mission)
        checkpoint = {
            "schemaVersion": "1",
            "cycle": 1,
            "missionDigest": mission_digest,
            "messages": [],
        }
        on_checkpoint = kwargs["on_mission_checkpoint"]
        if not callable(on_checkpoint):
            raise TypeError("mission checkpoint callback is missing")
        await on_checkpoint(
            mission,
            mission_digest,
            1,
            "respond",
            checkpoint,
            _digest(checkpoint),
        )
        return AgentRunOutput(
            content="The reviewed build is ready for current engineering validation.",
            model="native-handoff-fixture",
            input_tokens=1,
            output_tokens=1,
            engineering_mission=mission,
            digest=mission_digest,
            cycle_disposition="respond",
        )


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


def _wait_for_run(store: HostStore, run_id: str) -> None:
    terminal = {
        RunState.WAITING_FOR_USER,
        RunState.WAITING_FOR_APPROVAL,
        RunState.COMPLETED,
        RunState.CANCELLED,
        RunState.FAILED,
        RunState.QUARANTINED,
    }
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        run = store.get_run(run_id)
        if run is not None and run.state in terminal:
            return
        time.sleep(0.01)
    raise AssertionError(f"Agent run did not become terminal: {run_id}")


class NativeHandoffLineageTests(unittest.TestCase):
    def test_stored_handoff_seeds_first_engineering_mission_without_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-native-mission-") as raw:
            root = Path(raw)
            operation = _completed_operation(root)
            result = copy.deepcopy(operation.result)
            simulation = result["simulationVerification"]
            receipt = simulation["receipt"]
            project_hash = result["projectSha256"]
            receipt["buildBundleId"] = operation.request["buildBundle"]["bundleId"]
            receipt["reviewedPlanDigest"] = operation.request["reviewedPlan"][
                "planDigest"
            ]
            receipt["projectReadback"] = {
                "projectSha256": project_hash,
                "projectUnchangedDuringRuntime": True,
            }
            simulation_digest = _digest(receipt)
            simulation["trial"]["receiptDigest"] = simulation_digest
            simulation["trial"]["decision"]["receiptDigest"] = simulation_digest
            operation = NativeProjectOperation(
                **{
                    **operation.__dict__,
                    "result": result,
                }
            )
            workspace = result["workspaceRegistration"]["workspace"]
            reviewed_plan = operation.request["reviewedPlan"]
            build_bundle = operation.request["buildBundle"]
            build_receipt = result["buildBundleReceipt"]
            trial = simulation["trial"]

            config = HostConfig(
                database_path=root / "host.sqlite3",
                knowledge_database_path=root / "knowledge.sqlite3",
                static_dir=root / "missing-web-dist",
                event_poll_interval_seconds=0.01,
                event_heartbeat_seconds=0.05,
                launch_token="native-handoff-mission-test-launch-token",
            )
            store = HostStore(config.database_path)
            timestamp = datetime(2026, 8, 16, tzinfo=timezone.utc).isoformat()
            project_brief = {
                "performance_and_acceptance": (
                    "The current engineering validation satisfies the explicit "
                    "500 ms acceptance limit."
                )
            }
            with store._connection() as connection, connection:
                connection.execute(
                    """
                    INSERT INTO conversations(
                        id, title, workflow, project_brief_json,
                        created_at, updated_at
                    ) VALUES (?, ?, 'new_project_planning', ?, ?, ?)
                    """,
                    (
                        reviewed_plan["conversationId"],
                        "Reviewed native plan",
                        json.dumps(project_brief, separators=(",", ":")),
                        timestamp,
                        timestamp,
                    ),
                )

            repository = NativeProjectRepository(config.database_path)
            _insert(repository, operation)
            store.set_host_state("active_workspace", workspace)
            provenance = EngineeringConversationHandoffV1.model_validate({
                "schemaVersion": "1",
                "nativeProjectId": operation.id,
                "planningConversationId": reviewed_plan["conversationId"],
                "planningMessageId": reviewed_plan["messageId"],
                "reviewedPlanDigest": reviewed_plan["planDigest"],
                "projectBriefDigest": reviewed_plan["projectBriefDigest"],
                "attachmentRevisionDigest": reviewed_plan[
                    "attachmentRevisionDigest"
                ],
                "buildBundleId": build_bundle["bundleId"],
                "buildReceiptSchemaVersion": build_receipt["schemaVersion"],
                "buildReceiptDigest": _digest(build_receipt),
                "simulationReceiptSchemaVersion": receipt["schemaVersion"],
                "simulationReceiptDigest": simulation_digest,
                "simulationTrialId": trial["trialId"],
                "acceptedByActorId": trial["decision"]["actorId"],
                "acceptedAt": trial["decision"]["decidedAt"],
                "projectSha256": workspace["working_copy"]["current_sha256"],
            }).model_dump(mode="json", by_alias=True)
            conversation, created = store.create_engineering_handoff_conversation(
                title="Native engineering validation",
                native_project_id=operation.id,
                planning_conversation_id=reviewed_plan["conversationId"],
                workspace_id=workspace["workspace_id"],
                revision_id=workspace["revision_id"],
                engineering_session_id="native-handoff-session",
                provenance=provenance,
            )
            self.assertTrue(created)

            model_repository = ModelConnectionRepository(store)
            model_repository.save(
                base_url="http://localhost:4000/v1",
                model="native-handoff-fixture",
                timeout_seconds=30,
                api_key="fixture-key-not-sent-to-a-model",
                custom_headers={},
                capability_verified_at=datetime.now(timezone.utc),
                allow_insecure_http=True,
            )
            runtime = _NativeHandoffRuntime(store)
            mcp = _NativeHandoffMCP(workspace)
            app = create_app(
                config,
                store=store,
                mcp=mcp,  # type: ignore[arg-type]
                model_repository=model_repository,
                runtime_factory=lambda _settings, _broker: runtime,
            )
            reviewed_artifact = {
                "schemaVersion": "2",
                "objective": "Validate the exact reviewed conveyor control objective.",
                "sections": [
                    {
                        "sectionId": "overview",
                        "items": [
                            {
                                "verificationMethod": (
                                    "Inspect the current reviewed LD behavior."
                                )
                            },
                            {
                                "verificationMethod": (
                                    "Inspect the current reviewed LD behavior."
                                )
                            },
                        ],
                    },
                    {
                        "sectionId": "validation",
                        "items": [
                            {"verificationMethod": "Verify current interlock coverage."}
                        ],
                    },
                ],
            }
            review_calls: list[dict[str, object]] = []

            def require_reviewed_project_plan(**values: object) -> tuple[object, dict[str, object]]:
                review_calls.append(dict(values))
                self.assertEqual(
                    values,
                    {
                        "conversation_id": reviewed_plan["conversationId"],
                        "message_id": reviewed_plan["messageId"],
                        "plan_digest": reviewed_plan["planDigest"],
                        "reviewed_section_ids": reviewed_plan[
                            "reviewedSectionIds"
                        ],
                        "reviewed_item_ids": reviewed_plan["reviewedItemIds"],
                        "project_brief_digest": reviewed_plan[
                            "projectBriefDigest"
                        ],
                        "attachment_revision_digest": reviewed_plan[
                            "attachmentRevisionDigest"
                        ],
                        "expected_handoff_native_project_id": operation.id,
                    },
                )
                return object(), reviewed_artifact

            with patch.object(
                store,
                "require_reviewed_project_plan",
                side_effect=require_reviewed_project_plan,
            ), TestClient(app, base_url="http://localhost:8765") as client:
                _authenticate(client, config)
                attached = client.post(
                    "/api/v1/xg5000/attach",
                    json={
                        "pid": 41,
                        "hwnd": 42,
                        "projectPath": workspace["working_copy"]["path"],
                        "projectHash": workspace["working_copy"]["current_sha256"],
                    },
                )
                self.assertEqual(attached.status_code, 200, attached.text)
                response = client.post(
                    f"/api/v1/conversations/{conversation.id}/messages",
                    headers={"Idempotency-Key": "native-handoff-mission-run"},
                    json={
                        "content": "Replace the reviewed objective and rebuild everything.",
                        "operationId": "native-handoff-mission-run",
                        "changeRequestScope": "analysis_only",
                    },
                )
                self.assertEqual(response.status_code, 202, response.text)
                run_id = str(response.json()["runId"])
                _wait_for_run(store, run_id)
                finished_run = store.get_run(run_id)
                assert finished_run is not None
                self.assertNotEqual(
                    finished_run.state,
                    RunState.FAILED,
                    f"{finished_run.error_code}: {finished_run.error_detail}",
                )

            self.assertEqual(len(review_calls), 2)
            self.assertEqual(len(runtime.calls), 1)
            status = runtime.calls[0]
            self.assertEqual(
                status["objective"],
                reviewed_artifact["objective"],
            )
            self.assertEqual(
                status["completionCriteria"],
                [
                    "Inspect the current reviewed LD behavior.",
                    "Verify current interlock coverage.",
                    project_brief["performance_and_acceptance"],
                ],
            )
            self.assertEqual(
                status["completedStages"],
                [
                    "reviewed_plan",
                    "native_build",
                    "independent_reopen",
                    "xg_sim_acceptance",
                ],
            )
            self.assertEqual(status["remainingStages"], ["engineering_validation"])
            self.assertEqual(runtime.evidence_before_checkpoint, [])
            self.assertEqual(
                store.list_run_evidence(
                    conversation_id=conversation.id,
                    run_id=run_id,
                ),
                [],
            )
            mission = store.get_engineering_mission(conversation.id)
            self.assertIsNotNone(mission)
            assert mission is not None
            self.assertEqual(
                mission.engineering_mission["objective"],
                reviewed_artifact["objective"],
            )
            self.assertEqual(
                mission.engineering_mission["completionCriteria"],
                status["completionCriteria"],
            )
            self.assertEqual(
                mission.engineering_mission["steps"][0]["evidenceRefs"],
                [],
            )

    def test_generic_local_file_attachment_is_not_handoff_candidate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-local-handoff-") as raw:
            root = Path(raw)
            operation = _completed_operation(root)
            local_result = copy.deepcopy(operation.result)
            workspace = local_result["workspaceRegistration"]["workspace"]
            workspace["source_kind"] = "local_file"
            workspace["provenance"] = None
            local_operation = NativeProjectOperation(
                **{
                    **operation.__dict__,
                    "result": local_result,
                }
            )
            repository = NativeProjectRepository(root / "operations.sqlite3")
            _insert(repository, local_operation)

            with patch.object(
                native_projects,
                "_native_simulator_evidence_verified",
                return_value=True,
            ):
                self.assertEqual(repository.engineering_handoff_candidates(), [])
            self.assertIsNone(validated_new_build_workspace_provenance(local_operation, workspace))

    def test_exact_new_build_candidate_remains_handoff_compatible(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-build-handoff-") as raw:
            root = Path(raw)
            operation = _completed_operation(root)
            workspace = operation.result["workspaceRegistration"]["workspace"]
            expected = workspace["provenance"]
            self.assertEqual(
                validated_new_build_workspace_provenance(operation, workspace),
                expected,
            )

            # Active workspaces pass through a Pydantic model dump, which uses
            # the Python field name for the explicitly aliased schema field.
            active_workspace = copy.deepcopy(workspace)
            active_provenance = active_workspace["provenance"]
            active_provenance["receipt_schema"] = active_provenance.pop("schema")
            self.assertEqual(
                validated_new_build_workspace_provenance(
                    operation,
                    active_workspace,
                ),
                expected,
            )

            repository = NativeProjectRepository(root / "operations.sqlite3")
            _insert(repository, operation)
            with patch.object(
                native_projects,
                "_native_simulator_evidence_verified",
                return_value=True,
            ):
                self.assertEqual(
                    [item.id for item in repository.engineering_handoff_candidates()],
                    [operation.id],
                )

    def test_tampered_workspace_identity_cannot_reuse_valid_build_receipts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-tampered-handoff-") as raw:
            root = Path(raw)
            operation = _completed_operation(root)
            workspace = operation.result["workspaceRegistration"]["workspace"]

            tampered_provenance = copy.deepcopy(workspace)
            tampered_provenance["provenance"]["build_bundle_id"] = "9" * 64

            tampered_path = copy.deepcopy(workspace)
            tampered_path["source"]["original_path"] = str(root / "other.xgwx")

            tampered_revision = copy.deepcopy(workspace)
            tampered_revision["revision_id"] = "not-a-revision"

            for tampered in (
                tampered_provenance,
                tampered_path,
                tampered_revision,
            ):
                with self.subTest(tampered=tampered):
                    self.assertIsNone(
                        validated_new_build_workspace_provenance(operation, tampered)
                    )


if __name__ == "__main__":
    unittest.main()
