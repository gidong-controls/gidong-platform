from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from controlstudio_host.api_models import ResumeNativeProjectRequestV1
from controlstudio_host.native_projects import (
    NativeProjectConflict,
    NativeProjectOperation,
    NativeProjectPreconditionError,
    NativeProjectService,
    NativeProjectSpec,
    _native_project_plan_digest,
    _NativeProjectRequest,
    _pre_dispatch_native_simulation_failure,
    _require_empty_native_project_destination,
    _stored_creation_receipt_matches_reviewed_build,
    _validate_native_project_request,
)
from controlstudio_host.store import HostStore

_REQUEST_DIGEST = "a" * 64
_PLAN_DIGEST = "b" * 64
_OPERATION_ID = "11111111-1111-4111-8111-111111111111"


class _Repository:
    def __init__(self) -> None:
        destination = Path.cwd()
        self.operation = NativeProjectOperation(
            id=_OPERATION_ID,
            operation_id=_OPERATION_ID,
            state="QUARANTINED",
            request_digest=_REQUEST_DIGEST,
            actor_id="actor-1",
            request={
                "xg5000Pid": 101,
                "xg5000Hwnd": 202,
                "projectName": "ResumeProject",
                "workspaceParent": str(destination.parent),
                "plcSeries": "XGK",
                "cpuModel": "XGK-CPUH",
                "programName": "MainProgram",
                "language": "LD",
                "description": "reviewed",
                "buildBundle": {"bundleId": "bundle-1"},
            },
            plan_digest=_PLAN_DIGEST,
            destination_directory=str(destination),
            result={"preserved": True},
            error_code="NATIVE_PROJECT_MCP_REJECTED",
            error_detail="program check layout",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            completed_at=None,
        )

    def get(self, _operation_id: str) -> NativeProjectOperation:
        return self.operation

    def begin_resume(self, _operation_id: str) -> bool:
        self.operation = replace(self.operation, state="EXECUTING")
        return True

    def begin_completed_verification(self, _operation_id: str) -> bool:
        self.operation = replace(self.operation, state="EXECUTING")
        return True

    def quarantine(self, _operation_id: str, *, error_code: str, error_detail: str, result: dict):
        self.operation = replace(
            self.operation,
            state="QUARANTINED",
            result=result,
            error_code=error_code,
            error_detail=error_detail,
        )
        return self.operation

    def checkpoint_execution(self, _operation_id: str, result: dict):
        self.operation = replace(self.operation, result=result)
        return self.operation

    def complete(self, _operation_id: str, result: dict):
        self.operation = replace(self.operation, state="COMPLETED", result=result)
        return self.operation


class _Gateway:
    def __init__(self) -> None:
        self.workspace_calls: list[str] = []
        self.resume_result: dict | None = None

    async def call_workspace_tool(self, name: str, _arguments: dict):
        self.workspace_calls.append(name)
        if self.resume_result is not None:
            return self.resume_result
        raise RuntimeError("controlled program-check failure")

    def sign_mutation_authorization(self, claims: dict) -> dict:
        return {**claims, "signature": "test"}


class NativeProjectResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_typed_pre_dispatch_simulation_failure_remains_resumable(self) -> None:
        raw = {
            "ok": False,
            "verified": False,
            "operation": "project.verify_native_simulation",
            "project_unchanged": True,
            "controllerConnected": False,
            "plcWritePerformed": False,
            "simulation_failure": {
                "schemaVersion": "controlstudio-native-simulation-failure/v1",
                "classification": "PRE_DISPATCH_READ_FAILURE",
                "phase": "target_status_before_program_write",
                "detail": "XG5000_UIA_TIMEOUT: target status read timed out",
                "runtimeError": {
                    "code": "XG5000_UIA_TIMEOUT",
                    "workerOperation": "inspect_buttons",
                    "timeoutSeconds": 4.0,
                    "dispatchState": "not_applicable",
                },
                "dispatch": {
                    "simulatorStart": "not_attempted",
                    "programWrite": "not_attempted",
                    "cpuRun": "not_attempted",
                },
                "baseline": {"capture": "not_attempted", "restore": "not_attempted"},
                "recovery": {
                    "cpuStop": "not_attempted",
                    "restorationVerified": False,
                    "failureStages": [],
                },
                "projectHashBefore": "a" * 64,
                "projectHashAfter": "a" * 64,
                "projectUnchanged": True,
            },
        }
        gateway = _Gateway()
        gateway.call_workspace_tool = AsyncMock(return_value=raw)
        service = NativeProjectService(_Repository(), gateway)  # type: ignore[arg-type]
        result = await service._verify_native_simulation(
            reopen_verification={
                "state": "verified",
                "session": {
                    "session_id": "session-1",
                    "pid": 303,
                    "hwnd": 404,
                    "project_path": "C:/work/ResumeProject.xgwx",
                    "project_hash": "a" * 64,
                },
                "workspace": {},
            },
            build_bundle={
                "bundleId": "b" * 64,
                "reviewedPlan": {"planDigest": "c" * 64},
                "compiledProgram": {},
                "engineeringPlan": {},
                "technicalConfiguration": {},
            },
            trial_id="trial-1",
        )

        self.assertEqual(result["state"], "not_verified")
        self.assertNotIn("trial", result)
        self.assertEqual(
            result["failure"]["classification"], "PRE_DISPATCH_READ_FAILURE"
        )

    def test_pre_dispatch_failure_rejects_any_program_write_attempt(self) -> None:
        raw = {
            "ok": False,
            "verified": False,
            "operation": "project.verify_native_simulation",
            "project_unchanged": True,
            "controllerConnected": False,
            "plcWritePerformed": False,
            "simulation_failure": {
                "schemaVersion": "controlstudio-native-simulation-failure/v1",
                "classification": "PRE_DISPATCH_READ_FAILURE",
                "phase": "target_status_before_program_write",
                "detail": "typed test failure",
                "runtimeError": {
                    "code": "XG5000_UIA_TIMEOUT",
                    "workerOperation": "inspect_buttons",
                    "timeoutSeconds": 4.0,
                    "dispatchState": "not_applicable",
                },
                "dispatch": {
                    "simulatorStart": "not_attempted",
                    "programWrite": "attempted",
                    "cpuRun": "not_attempted",
                },
                "baseline": {"capture": "not_attempted", "restore": "not_attempted"},
                "recovery": {
                    "cpuStop": "not_attempted",
                    "restorationVerified": False,
                    "failureStages": [],
                },
                "projectHashBefore": "a" * 64,
                "projectHashAfter": "a" * 64,
                "projectUnchanged": True,
            },
        }

        self.assertIsNone(
            _pre_dispatch_native_simulation_failure(raw, project_hash="a" * 64)
        )
        raw["simulation_failure"]["dispatch"]["programWrite"] = "not_attempted"
        raw["simulation_failure"]["runtimeError"]["dispatchState"] = "unknown"
        self.assertIsNone(
            _pre_dispatch_native_simulation_failure(raw, project_hash="a" * 64)
        )

    async def test_runtime_write_failure_preserves_phase_in_quarantined_trial(self) -> None:
        raw = {
            "ok": False,
            "verified": False,
            "operation": "project.verify_native_simulation",
            "project_unchanged": False,
            "controllerConnected": False,
            "plcWritePerformed": False,
            "simulation_failure": {
                "schemaVersion": "controlstudio-native-simulation-failure/v1",
                "classification": "UNRESOLVED_RUNTIME_FAILURE",
                "phase": "cpu_run",
                "detail": "XG-SIM RUN command was not verified",
                "runtimeError": {
                    "code": "SessionError",
                    "workerOperation": None,
                    "timeoutSeconds": None,
                    "dispatchState": None,
                },
                "dispatch": {
                    "simulatorStart": "attempted",
                    "programWrite": "attempted",
                    "cpuRun": "attempted",
                },
                "baseline": {"capture": "attempted", "restore": "attempted"},
                "recovery": {
                    "cpuStop": "attempted",
                    "restorationVerified": False,
                    "failureStages": [],
                },
                "projectHashBefore": "a" * 64,
                "projectHashAfter": "b" * 64,
                "projectUnchanged": False,
            },
        }
        gateway = _Gateway()
        gateway.call_workspace_tool = AsyncMock(return_value=raw)
        service = NativeProjectService(_Repository(), gateway)  # type: ignore[arg-type]
        result = await service._verify_native_simulation(
            reopen_verification={
                "state": "verified",
                "session": {
                    "session_id": "session-1",
                    "pid": 303,
                    "hwnd": 404,
                    "project_path": "C:/work/ResumeProject.xgwx",
                    "project_hash": "a" * 64,
                },
                "workspace": {},
            },
            build_bundle={
                "bundleId": "b" * 64,
                "reviewedPlan": {"planDigest": "c" * 64},
                "compiledProgram": {},
                "engineeringPlan": {},
                "technicalConfiguration": {},
            },
            trial_id="trial-1",
        )

        self.assertEqual(result["state"], "quarantined")
        self.assertEqual(result["failure"]["phase"], "cpu_run")
        self.assertEqual(result["failure"]["dispatch"]["programWrite"], "attempted")
        self.assertEqual(result["trial"]["state"], "QUARANTINED")
        self.assertFalse(result["trial"]["safeRestorationVerified"])

    async def test_completed_tail_does_not_rerun_an_unresolved_simulation_quarantine(self) -> None:
        repository = _Repository()
        repository.operation = replace(
            repository.operation,
            state="COMPLETED",
            result={
                "engineeredProgramReady": False,
                "workspaceBound": False,
                "workspaceRegistration": {
                    "state": "captured_not_bound",
                    "workspace": {"workspace_id": "workspace-1", "working_copy": {}},
                },
                "reopenVerification": {"state": "verified"},
                "simulationVerification": {
                    "state": "quarantined",
                    "trial": {"state": "QUARANTINED"},
                },
            },
        )
        gateway = _Gateway()
        service = NativeProjectService(repository, gateway)  # type: ignore[arg-type]
        with (
            patch(
                "controlstudio_host.native_projects._native_project_plan_digest",
                return_value=_PLAN_DIGEST,
            ),
            patch(
                "controlstudio_host.native_projects._native_bundle_description",
                return_value="reviewed",
            ),
            patch(
                "controlstudio_host.native_projects._stored_creation_receipt_matches_reviewed_build",
                return_value=True,
            ),
            self.assertRaisesRegex(NativeProjectConflict, "unresolved XG-SIM quarantine"),
        ):
            await service.resume(
                _OPERATION_ID,
                actor_id="actor-1",
                pid=101,
                hwnd=202,
                expected_request_digest=_REQUEST_DIGEST,
            )


    def test_completed_tail_reopened_target_fields_are_paired(self) -> None:
        with self.assertRaises(ValidationError):
            ResumeNativeProjectRequestV1.model_validate(
                {
                    "pid": 101,
                    "hwnd": 202,
                    "expectedRequestDigest": _REQUEST_DIGEST,
                    "reopenedPid": 303,
                }
            )
        request = ResumeNativeProjectRequestV1.model_validate(
            {
                "pid": 101,
                "hwnd": 202,
                "expectedRequestDigest": _REQUEST_DIGEST,
                "reopenedPid": 303,
                "reopenedHwnd": 404,
            }
        )
        self.assertEqual((request.reopened_pid, request.reopened_hwnd), (303, 404))

    def test_plan_digest_is_stable_after_the_destination_becomes_populated(self) -> None:
        with TemporaryDirectory() as temporary:
            destination = Path(temporary).resolve()
            request = _NativeProjectRequest(
                project_name="ResumeProject",
                destination_directory=destination,
                plc_series="XGK",
                cpu_type="XGK-CPUH",
                program_name="MainProgram",
                program_language="LD",
                description="reviewed",
            )
            build_bundle = {"bundleId": "bundle-1", "compiledProgram": {"source": "LD"}}
            before_project_exists = _native_project_plan_digest(
                request,
                pid=101,
                hwnd=202,
                build_bundle=build_bundle,
            )
            (destination / "ResumeProject.xgwx").write_bytes(b"existing project")

            after_project_exists = _native_project_plan_digest(
                request,
                pid=101,
                hwnd=202,
                build_bundle=build_bundle,
            )

            self.assertEqual(after_project_exists, before_project_exists)
            validated = _validate_native_project_request(request)
            with self.assertRaisesRegex(
                NativeProjectPreconditionError,
                "destination_directory must be empty",
            ):
                _require_empty_native_project_destination(validated.destination_directory)

    def test_resume_reclaims_only_its_parked_native_project_create_lease(self) -> None:
        with TemporaryDirectory() as temporary:
            store = HostStore(Path(temporary) / "host.sqlite3")
            target_identity = {
                "operationId": _OPERATION_ID,
                "xg5000Pid": 101,
                "xg5000Hwnd": 202,
            }
            store.acquire_target_operation_lease(
                operation_id=_OPERATION_ID,
                owner_kind="native_project_create",
                holder_token="original-create-holder",
                target_identity=target_identity,
            )
            store.park_target_operation_lease(
                operation_id=_OPERATION_ID,
                holder_token="original-create-holder",
                reason="native_project_create_requires_recovery",
            )

            reclaimed = store.acquire_target_operation_lease(
                operation_id=_OPERATION_ID,
                owner_kind="native_project_create",
                holder_token="resume-holder",
                target_identity=target_identity,
            )

        self.assertEqual(reclaimed["operation_id"], _OPERATION_ID)
        self.assertEqual(reclaimed["owner_kind"], "native_project_create")
        self.assertEqual(reclaimed["holder_token"], "resume-holder")
        self.assertEqual(reclaimed["state"], "ACTIVE")

    async def test_resume_rejection_preserves_quarantine_and_never_recreates(self) -> None:
        repository = _Repository()
        gateway = _Gateway()
        service = NativeProjectService(repository, gateway)  # type: ignore[arg-type]
        with (
            patch.object(service, "_verify_exact_instance", new=AsyncMock()),
            patch("controlstudio_host.native_projects._native_project_plan_digest", return_value=_PLAN_DIGEST),
            patch("controlstudio_host.native_projects._native_bundle_description", return_value="reviewed"),
        ):
            operation = await service.resume(
                _OPERATION_ID,
                actor_id="actor-1",
                pid=101,
                hwnd=202,
                expected_request_digest=_REQUEST_DIGEST,
            )

        self.assertEqual(operation.state, "QUARANTINED")
        self.assertEqual(operation.error_code, "NATIVE_PROJECT_RESUME_REJECTED")
        self.assertTrue(operation.result["preserved"])
        self.assertEqual(gateway.workspace_calls, ["project.resume_native"])
        self.assertNotIn("project.create_native", gateway.workspace_calls)

    async def test_stale_request_digest_rejects_before_any_native_call(self) -> None:
        repository = _Repository()
        gateway = _Gateway()
        service = NativeProjectService(repository, gateway)  # type: ignore[arg-type]

        with self.assertRaisesRegex(NativeProjectConflict, "stale"):
            await service.resume(
                _OPERATION_ID,
                actor_id="actor-1",
                pid=101,
                hwnd=202,
                expected_request_digest="c" * 64,
            )

        self.assertEqual(repository.operation.state, "QUARANTINED")
        self.assertEqual(gateway.workspace_calls, [])

    async def test_stale_stored_plan_rejects_before_any_native_call(self) -> None:
        repository = _Repository()
        gateway = _Gateway()
        service = NativeProjectService(repository, gateway)  # type: ignore[arg-type]
        with (
            patch("controlstudio_host.native_projects._native_project_plan_digest", return_value="c" * 64),
            patch("controlstudio_host.native_projects._native_bundle_description", return_value="reviewed"),
            self.assertRaisesRegex(NativeProjectConflict, "plan digest is stale"),
        ):
            await service.resume(
                _OPERATION_ID,
                actor_id="actor-1",
                pid=101,
                hwnd=202,
                expected_request_digest=_REQUEST_DIGEST,
            )

        self.assertEqual(repository.operation.state, "QUARANTINED")
        self.assertEqual(gateway.workspace_calls, [])

    async def test_verified_resume_enters_capture_reopen_and_simulation_tail(self) -> None:
        repository = _Repository()
        gateway = _Gateway()
        gateway.resume_result = {"ok": True}
        service = NativeProjectService(repository, gateway)  # type: ignore[arg-type]
        resumed_result = {
            "identityVerified": True,
            "projectPath": "C:/work/ResumeProject-000000000000/ResumeProject.xgwx",
            "projectSha256": "d" * 64,
            "buildBundleReceipt": {"receipt": True},
        }
        capture = AsyncMock(return_value={"state": "capture_failed"})
        reopen = AsyncMock(return_value={"state": "not_verified"})
        simulation = AsyncMock(return_value={"state": "not_verified"})
        with (
            patch.object(service, "_verify_exact_instance", new=AsyncMock()),
            patch("controlstudio_host.native_projects._native_project_plan_digest", return_value=_PLAN_DIGEST),
            patch("controlstudio_host.native_projects._native_bundle_description", return_value="reviewed"),
            patch("controlstudio_host.native_projects._new_build_workspace_provenance", return_value={}),
            patch.object(service, "_validate_resume_result", return_value=resumed_result),
            patch.object(service, "_capture_workspace", new=capture),
            patch.object(service, "_verify_fresh_process_reopen", new=reopen),
            patch.object(service, "_verify_native_simulation", new=simulation),
        ):
            operation = await service.resume(
                _OPERATION_ID,
                actor_id="actor-1",
                pid=101,
                hwnd=202,
                expected_request_digest=_REQUEST_DIGEST,
            )

        self.assertEqual(operation.state, "COMPLETED", operation.error_detail)
        self.assertEqual(gateway.workspace_calls, ["project.resume_native"])
        capture.assert_awaited_once()
        reopen.assert_awaited_once()
        simulation.assert_awaited_once()

    async def test_completed_unbound_resume_reuses_only_the_controlled_reopen_tail(self) -> None:
        repository = _Repository()
        repository.operation = replace(
            repository.operation,
            state="COMPLETED",
            result={
                "identityVerified": True,
                "projectPath": "C:/work/ResumeProject-000000000000/ResumeProject.xgwx",
                "projectSha256": "d" * 64,
                "buildBundleReceipt": {"receipt": True},
                "engineeredProgramReady": False,
                "workspaceBound": False,
                "workspaceRegistration": {
                    "state": "captured_not_bound",
                    "workspace": {"workspace_id": "workspace-1", "working_copy": {}},
                },
                "reopenVerification": {"state": "not_verified"},
                "simulationVerification": {"state": "not_verified"},
            },
        )
        gateway = _Gateway()
        service = NativeProjectService(repository, gateway)  # type: ignore[arg-type]
        existing_session = {"session_id": "fresh-session", "pid": 303, "hwnd": 404}
        reopened = {
            "state": "verified",
            "workspace": repository.operation.result["workspaceRegistration"]["workspace"],
            "session": existing_session,
        }
        simulation = {
            "state": "not_verified",
            "detail": "XG-SIM is not yet observed",
        }
        with (
            patch("controlstudio_host.native_projects._native_project_plan_digest", return_value=_PLAN_DIGEST),
            patch("controlstudio_host.native_projects._native_bundle_description", return_value="reviewed"),
            patch("controlstudio_host.native_projects._stored_creation_receipt_matches_reviewed_build", return_value=True),
            patch.object(service, "_rebind_completed_reopen_target", new=AsyncMock(return_value=existing_session)) as rebind,
            patch.object(service, "_verify_fresh_process_reopen", new=AsyncMock(return_value=reopened)) as verify,
            patch.object(service, "_verify_native_simulation", new=AsyncMock(return_value=simulation)) as verify_sim,
        ):
            operation = await service.resume(
                _OPERATION_ID,
                actor_id="actor-1",
                pid=101,
                hwnd=202,
                expected_request_digest=_REQUEST_DIGEST,
                reopened_pid=303,
                reopened_hwnd=404,
            )

        self.assertEqual(operation.state, "COMPLETED")
        self.assertFalse(operation.result["engineeredProgramReady"])
        self.assertEqual(
            operation.result["resumeProvenance"],
            {
                "lifecycleMode": "completed_post_create_verification_continuation",
                "noNewDialog": True,
                "noIlInsert": True,
                "reusedControlledReopen": {"pid": 303, "hwnd": 404},
            },
        )
        rebind.assert_awaited_once()
        verify.assert_awaited_once()
        self.assertEqual(verify.await_args.kwargs["existing_session"], existing_session)
        verify_sim.assert_awaited_once()
        self.assertEqual(gateway.workspace_calls, [])

    async def test_completed_rebind_signs_and_requires_saved_build_read_only_binding(self) -> None:
        """A current file-use observation is not itself controlled-launch authority."""

        repository = _Repository()
        project_hash = "d" * 64
        workspace_id = "22222222-2222-4222-8222-222222222222"
        revision_id = "33333333-3333-4333-8333-333333333333"
        project_path = "C:/work/ResumeProject-000000000000/ResumeProject.xgwx"
        repository.operation = replace(
            repository.operation,
            state="COMPLETED",
            result={"buildBundleReceipt": {"receipt": True}},
        )
        gateway = _Gateway()
        service = NativeProjectService(repository, gateway)  # type: ignore[arg-type]
        observed = {
            "session_id": "observed",
            "pid": 303,
            "hwnd": 404,
            "project_path": project_path,
            "project_hash": project_hash,
            "metadata": {
                "title": "ResumeProject - XG5000",
                "project_identity_proof": {
                    "method": "selected_pid_exact_window_title_native_file_use_v1",
                    "pid": 303,
                    "main_hwnd": 404,
                    "project_path": project_path,
                    "project_hash": project_hash,
                    "process_create_time": 123.0,
                }
            },
        }

        seen_authorizations: list[dict] = []

        async def read_tool(name: str, arguments: dict) -> dict:
            if name == "xg5000.instances":
                return {"instances": [{"pid": 303, "hwnd": 404, "visible": True}]}
            self.assertEqual(name, "xg5000.attach")
            if "resume_authorization" not in arguments:
                return {"session": observed}
            authorization = arguments["resume_authorization"]
            seen_authorizations.append(authorization)
            return {
                "session": {
                    "session_id": "saved-build-bound",
                    "pid": 303,
                    "hwnd": 404,
                    "project_path": project_path,
                    "project_hash": project_hash,
                    "metadata": {
                        "project_identity_read_only_bound_to_selected_pid": True,
                        "project_identity_assurance": "controlled_launch_read_only",
                        "saved_build_reopen_resume": {
                            "purpose": authorization["purpose"],
                            "native_project_id": authorization["native_project_id"],
                            "source_kind": "new_build",
                            "provenance_receipt_digest": authorization[
                                "workspace_provenance_receipt_digest"
                            ],
                            "workspace_id": authorization["workspace_id"],
                            "revision_id": authorization["revision_id"],
                            "pid": authorization["pid"],
                            "main_hwnd": authorization["hwnd"],
                            "project_path": authorization["project_path"],
                            "project_hash": authorization["project_hash"],
                            "process_create_time": authorization["process_create_time"],
                            "authorization_id": authorization["authorization_id"],
                            "build_receipt_digest": authorization["build_receipt_digest"],
                        },
                    },
                }
            }

        gateway.call_read_tool = read_tool  # type: ignore[attr-defined]
        registration = {
            "workspace": {
                "workspace_id": workspace_id,
                "revision_id": revision_id,
                "working_copy": {"path": project_path, "current_sha256": project_hash},
            }
        }
        provenance = {"receipt_digest": "e" * 64}
        with patch(
            "controlstudio_host.native_projects.validated_new_build_workspace_provenance",
            return_value=provenance,
        ):
            session = await service._rebind_completed_reopen_target(
                operation=repository.operation,
                actor_id="actor-1",
                source_pid=101,
                reopened_pid=303,
                reopened_hwnd=404,
                registration=registration,
        )

        self.assertEqual(session["session_id"], "saved-build-bound")
        # _Gateway preserves claims directly; inspect the rebind request to avoid
        # accepting a live file-use observation as a controlled binding.
        self.assertEqual(len(seen_authorizations), 1)
        self.assertEqual(seen_authorizations[0]["native_project_id"], _OPERATION_ID)
        self.assertNotIn("trial_id", seen_authorizations[0])
        self.assertNotIn("receipt_digest", seen_authorizations[0])
        self.assertEqual(session["metadata"]["saved_build_reopen_resume"]["purpose"], "saved_build_reopen_readback")
        self.assertEqual(session["metadata"]["saved_build_reopen_resume"]["source_kind"], "new_build")
        self.assertNotIn("schema", session["metadata"]["saved_build_reopen_resume"])
        self.assertNotIn("trial_id", session["metadata"]["saved_build_reopen_resume"])
        self.assertEqual(
            session["metadata"]["saved_build_reopen_resume"]["build_receipt_digest"],
            seen_authorizations[0]["build_receipt_digest"],
        )

    async def test_completed_rebind_reports_gateway_stage_without_checkpointing(self) -> None:
        repository = _Repository()
        repository.operation = replace(
            repository.operation,
            state="COMPLETED",
            result={"buildBundleReceipt": {"receipt": True}},
        )
        gateway = _Gateway()

        async def unavailable(_name: str, _arguments: dict) -> dict:
            raise RuntimeError("transport unavailable")

        gateway.call_read_tool = unavailable  # type: ignore[attr-defined]
        service = NativeProjectService(repository, gateway)  # type: ignore[arg-type]
        with (
            patch(
                "controlstudio_host.native_projects.validated_new_build_workspace_provenance",
                return_value={"receipt_digest": "e" * 64},
            ),
            self.assertRaisesRegex(NativeProjectConflict, "instance inventory"),
        ):
            await service._rebind_completed_reopen_target(
                operation=repository.operation,
                actor_id="actor-1",
                source_pid=101,
                reopened_pid=303,
                reopened_hwnd=404,
                registration={
                    "workspace": {
                        "workspace_id": "22222222-2222-4222-8222-222222222222",
                        "revision_id": "33333333-3333-4333-8333-333333333333",
                        "working_copy": {
                            "path": "C:/work/ResumeProject.xgwx",
                            "current_sha256": "d" * 64,
                        },
                    }
                },
            )
        self.assertEqual(repository.operation.state, "COMPLETED")

    async def test_completed_unbound_resume_rejects_before_tail_without_reopened_target(self) -> None:
        repository = _Repository()
        repository.operation = replace(
            repository.operation,
            state="COMPLETED",
            result={
                "identityVerified": True,
                "projectPath": "C:/work/ResumeProject-000000000000/ResumeProject.xgwx",
                "projectSha256": "d" * 64,
                "buildBundleReceipt": {"receipt": True},
                "engineeredProgramReady": False,
                "workspaceBound": False,
                "workspaceRegistration": {
                    "state": "captured_not_bound",
                    "workspace": {"workspace_id": "workspace-1", "working_copy": {}},
                },
                "reopenVerification": {"state": "not_verified"},
            },
        )
        gateway = _Gateway()
        service = NativeProjectService(repository, gateway)  # type: ignore[arg-type]

        with (
            patch("controlstudio_host.native_projects._native_project_plan_digest", return_value=_PLAN_DIGEST),
            patch("controlstudio_host.native_projects._native_bundle_description", return_value="reviewed"),
            patch("controlstudio_host.native_projects._stored_creation_receipt_matches_reviewed_build", return_value=True),
            self.assertRaisesRegex(NativeProjectConflict, "reopened PID/HWND"),
        ):
            await service.resume(
                _OPERATION_ID,
                actor_id="actor-1",
                pid=101,
                hwnd=202,
                expected_request_digest=_REQUEST_DIGEST,
            )

        self.assertEqual(repository.operation.state, "COMPLETED")
        self.assertEqual(gateway.workspace_calls, [])

    async def test_completed_unbound_resume_rebinds_the_failed_observed_target(self) -> None:
        repository = _Repository()
        project_path = "C:/work/ResumeProject.xgwx"
        project_hash = "d" * 64
        session = {"session_id": "failed-readback", "pid": 303, "hwnd": 404}
        registration = {
            "state": "captured_not_bound",
            "workspace": {
                "workspace_id": "workspace-1",
                "working_copy": {"path": project_path, "current_sha256": project_hash},
            },
        }
        repository.operation = replace(
            repository.operation,
            state="COMPLETED",
            result={
                "identityVerified": True,
                "projectPath": "C:/work/ResumeProject-000000000000/ResumeProject.xgwx",
                "projectSha256": "d" * 64,
                "buildBundleReceipt": {"receipt": True},
                "engineeredProgramReady": False,
                "workspaceBound": False,
                "workspaceRegistration": registration,
                "reopenVerification": {
                    "state": "not_verified",
                    "reopenTarget": {
                        "pid": 303,
                        "hwnd": 404,
                        "processCreateTime": 123.0,
                        "projectPath": project_path,
                        "projectSha256": project_hash,
                        "session": session,
                    },
                },
                "simulationVerification": {"state": "not_verified"},
            },
        )
        gateway = _Gateway()
        service = NativeProjectService(repository, gateway)  # type: ignore[arg-type]
        reopened = {"state": "not_verified", "workspace": registration["workspace"]}
        simulation = {"state": "not_verified"}
        rebound = {"session_id": "fresh-rebind", "pid": 303, "hwnd": 404}
        with (
            patch("controlstudio_host.native_projects._native_project_plan_digest", return_value=_PLAN_DIGEST),
            patch("controlstudio_host.native_projects._native_bundle_description", return_value="reviewed"),
            patch("controlstudio_host.native_projects._stored_creation_receipt_matches_reviewed_build", return_value=True),
            patch.object(service, "_rebind_completed_reopen_target", new=AsyncMock(return_value=rebound)) as rebind,
            patch.object(service, "_verify_fresh_process_reopen", new=AsyncMock(return_value=reopened)),
            patch.object(service, "_verify_native_simulation", new=AsyncMock(return_value=simulation)),
        ):
            await service.resume(
                _OPERATION_ID,
                actor_id="actor-1",
                pid=101,
                hwnd=202,
                expected_request_digest=_REQUEST_DIGEST,
            )

        self.assertEqual(rebind.await_args.kwargs["reopened_pid"], 303)
        self.assertEqual(rebind.await_args.kwargs["reopened_hwnd"], 404)
        self.assertEqual(rebind.await_args.kwargs["expected_process_create_time"], 123.0)

    def test_failed_reopen_target_evidence_is_not_reopen_verification(self) -> None:
        session = {
            "session_id": "failed-readback",
            "pid": 303,
            "hwnd": 404,
            "project_path": "C:/work/ResumeProject.xgwx",
            "project_hash": "d" * 64,
            "metadata": {
                "saved_build_reopen_resume": {"process_create_time": 123.0},
            },
        }
        target = NativeProjectService._failed_reopen_target_evidence(
            session,
            pid=303,
            hwnd=404,
            project_path="C:/work/ResumeProject.xgwx",
            project_hash="d" * 64,
            launch={"reused_existing_controlled_process": True},
        )

        self.assertEqual(target["processCreateTime"], 123.0)  # type: ignore[index]
        self.assertEqual(target["session"], session)  # type: ignore[index]
        self.assertNotIn("state", target)  # type: ignore[arg-type]

    async def test_completed_resume_reuses_verified_reopen_and_pending_trial(self) -> None:
        repository = _Repository()
        session = {"session_id": "fresh-session", "pid": 303, "hwnd": 404}
        registration = {
            "state": "captured_not_bound",
            "workspace": {"workspace_id": "workspace-1", "working_copy": {}},
        }
        reopen = {"state": "verified", "workspace": registration["workspace"], "session": session}
        simulation = {"state": "verified", "trial": {"state": "OBSERVING"}}
        repository.operation = replace(
            repository.operation,
            state="COMPLETED",
            result={
                "identityVerified": True,
                "projectPath": "C:/work/ResumeProject-000000000000/ResumeProject.xgwx",
                "projectSha256": "d" * 64,
                "buildBundleReceipt": {"receipt": True},
                "engineeredProgramReady": False,
                "workspaceBound": False,
                "workspaceRegistration": registration,
                "reopenVerification": reopen,
                "simulationVerification": simulation,
            },
        )
        gateway = _Gateway()
        service = NativeProjectService(repository, gateway)  # type: ignore[arg-type]
        final = replace(repository.operation, state="COMPLETED")
        with (
            patch("controlstudio_host.native_projects._native_project_plan_digest", return_value=_PLAN_DIGEST),
            patch("controlstudio_host.native_projects._native_bundle_description", return_value="reviewed"),
            patch("controlstudio_host.native_projects._stored_creation_receipt_matches_reviewed_build", return_value=True),
            patch.object(service, "_controlled_reopen_session_matches", return_value=True),
            patch.object(service, "_verify_fresh_process_reopen", new=AsyncMock()) as verify,
            patch.object(service, "_verify_native_simulation", new=AsyncMock()) as verify_sim,
            patch.object(service, "_complete_verified_native_tail", new=AsyncMock(return_value=(final, False))) as tail,
        ):
            operation = await service.resume(
                _OPERATION_ID,
                actor_id="actor-1",
                pid=101,
                hwnd=202,
                expected_request_digest=_REQUEST_DIGEST,
            )

        self.assertEqual(operation.state, "COMPLETED")
        verify.assert_not_awaited()
        verify_sim.assert_not_awaited()
        self.assertEqual(tail.await_args.kwargs["reopen_verification"], reopen)
        self.assertEqual(tail.await_args.kwargs["simulation_verification"], simulation)
        self.assertEqual(gateway.workspace_calls, [])

    async def test_resume_signing_failure_returns_to_quarantine_without_dispatch(self) -> None:
        repository = _Repository()
        gateway = _Gateway()
        service = NativeProjectService(repository, gateway)  # type: ignore[arg-type]
        with (
            patch.object(service, "_verify_exact_instance", new=AsyncMock()),
            patch("controlstudio_host.native_projects._native_project_plan_digest", return_value=_PLAN_DIGEST),
            patch("controlstudio_host.native_projects._native_bundle_description", return_value="reviewed"),
            patch.object(service, "_authorization", side_effect=RuntimeError("signing unavailable")),
        ):
            operation = await service.resume(
                _OPERATION_ID,
                actor_id="actor-1",
                pid=101,
                hwnd=202,
                expected_request_digest=_REQUEST_DIGEST,
            )

        self.assertEqual(operation.state, "QUARANTINED")
        self.assertEqual(operation.error_code, "NATIVE_PROJECT_RESUME_REJECTED")
        self.assertEqual(gateway.workspace_calls, [])

    async def test_new_authenticated_actor_can_resume_the_stored_request(self) -> None:
        repository = _Repository()
        gateway = _Gateway()
        service = NativeProjectService(repository, gateway)  # type: ignore[arg-type]
        with (
            patch.object(service, "_verify_exact_instance", new=AsyncMock()),
            patch("controlstudio_host.native_projects._native_project_plan_digest", return_value=_PLAN_DIGEST),
            patch("controlstudio_host.native_projects._native_bundle_description", return_value="reviewed"),
        ):
            operation = await service.resume(
                _OPERATION_ID,
                actor_id="browser:rotated-session",
                pid=101,
                hwnd=202,
                expected_request_digest=_REQUEST_DIGEST,
            )

        self.assertEqual(operation.state, "QUARANTINED")
        self.assertEqual(gateway.workspace_calls, ["project.resume_native"])

    def test_resume_accepts_the_full_program_check_data_mapping(self) -> None:
        with TemporaryDirectory() as temporary:
            destination = Path(temporary).resolve()
            project = destination / "ResumeProject.xgwx"
            project.write_bytes(b"verified native project")
            project_hash = hashlib.sha256(project.read_bytes()).hexdigest()
            source = "LD M0000\nEND"
            plan_digest = "b" * 64
            bundle = {
                "bundleId": "bundle-1",
                "reviewedPlan": {"planDigest": "c" * 64},
                "compiledProgram": {
                    "source": source,
                    "sourceSha256": hashlib.sha256(source.encode()).hexdigest(),
                    "rungIds": ["rung-1"],
                    "ladderIrSha256": "d" * 64,
                },
            }
            spec = NativeProjectSpec(
                operation_id=_OPERATION_ID,
                project_name="ResumeProject",
                workspace_parent=destination.parent,
                plc_series="XGK",
                cpu_model="XGK-CPUH",
                program_name="MainProgram",
                language="LD",
                description="reviewed",
                xg5000_pid=101,
                xg5000_hwnd=202,
            )
            authorization = {
                "authorization_id": "auth-1",
                "nonce": "nonce-1",
                "actor_id": "browser:current",
                "plan_digest": plan_digest,
                "build_bundle_id": "bundle-1",
            }
            check_data = {
                "status": "passed",
                "error_count": 0,
                "program_id": "program-id",
                "program_name": "MainProgram",
                "command": {"profile_id": "real-native-command"},
                "fresh_transition": {"fresh": True, "kind": "pane"},
            }
            receipt = {
                "schemaVersion": "controlstudio-native-build-receipt/v3",
                "bundleId": "bundle-1",
                "phase": "engineered_control_program",
                "status": "APPLIED_VERIFIED",
                "reviewedPlanDigest": "c" * 64,
                "nativeReadback": {
                    "projectName": "ResumeProject",
                    "projectPath": str(project.resolve()),
                    "projectSha256": project_hash,
                    "projectSize": project.stat().st_size,
                    "destinationDirectory": str(destination.resolve()),
                    "plcSeries": "XGK",
                    "cpuModel": "XGK-CPUH",
                    "programName": "MainProgram",
                    "language": "LD",
                    "description": "reviewed",
                    "identityVerified": True,
                },
                "programReadback": {
                    "rungCount": 1,
                    "rungIds": ["rung-1"],
                    "ladderIrSha256": "d" * 64,
                    "sourceSha256": bundle["compiledProgram"]["sourceSha256"],
                    "exactInstructionReadback": source.splitlines(),
                    "finalView": "LD",
                    "programCheckPassed": True,
                    "savedProjectSha256": project_hash,
                },
            }
            raw = {
                "ok": True,
                "verified": True,
                "operation": "project.resume_native",
                "pid": 101,
                "hwnd": 202,
                "plan_digest": plan_digest,
                "controller_connected": False,
                "plc_write_performed": False,
                "resume_provenance": {
                    "mode": "observed_existing_project",
                    "lifecycle_mode": "resume_existing_verified_source",
                    "no_new_dialog": True,
                    "no_il_insert": True,
                    "observed_program_id": "program-id",
                    "observed_program_name": "MainProgram",
                },
                "authorization": {**authorization, "signature_verified": True, "consumed": True},
                "project_path": str(project),
                "project_sha256": project_hash,
                "project_size": project.stat().st_size,
                "program_apply": {
                    "status": "APPLIED_VERIFIED",
                    "source_sha256": bundle["compiledProgram"]["sourceSha256"],
                    "rung_ids": ["rung-1"],
                    "ladder_ir_sha256": "d" * 64,
                    "exact_instruction_readback": source.splitlines(),
                    "ld_roundtrip": {"roundtrip_il": source.splitlines(), "final_view": "LD"},
                    "program_check": check_data,
                    "save": {"hash_after": project_hash},
                    "saved_project_sha256": project_hash,
                },
                "program_check_receipt": {
                    "ok": True,
                    "verified": True,
                    "operation": "program.check_ld",
                    "pid": 101,
                    "hwnd": 202,
                    "data": check_data,
                },
                "build_bundle_receipt": receipt,
            }
            with patch("controlstudio_host.native_projects._native_bundle_description", return_value="reviewed"):
                result = NativeProjectService._validate_resume_result(
                    raw,
                    spec=spec,
                    destination=destination,
                    expected_path=project,
                    plan_digest=plan_digest,
                    authorization=authorization,
                    build_bundle=bundle,
                    prior_result={},
                )

            self.assertEqual(result["resumeProvenance"]["mcp"]["observed_program_name"], "MainProgram")
            self.assertEqual(raw["program_apply"]["program_check"], check_data)
            with patch("controlstudio_host.native_projects._native_bundle_description", return_value="reviewed"):
                self.assertTrue(
                    _stored_creation_receipt_matches_reviewed_build(
                        result,
                        request={
                            "xg5000Pid": 101,
                            "xg5000Hwnd": 202,
                            "projectName": "ResumeProject",
                            "plcSeries": "XGK",
                            "cpuModel": "XGK-CPUH",
                            "programName": "MainProgram",
                            "language": "LD",
                            "buildBundle": bundle,
                        },
                        operation_plan_digest=plan_digest,
                        expected_path=project,
                        destination=destination,
                    )
                )


if __name__ == "__main__":
    unittest.main()
