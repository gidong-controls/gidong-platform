from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from controlstudio_host.native_projects import (
    NativeProjectConflict,
    NativeProjectOperation,
    NativeProjectService,
)


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


if __name__ == "__main__":
    unittest.main()
