from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from controlstudio_host.native_projects import (
    NativeProjectConflict,
    NativeProjectOperation,
    NativeProjectService,
    NativeProjectSpec,
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


if __name__ == "__main__":
    unittest.main()
