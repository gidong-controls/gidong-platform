from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import FakeMCP, authenticate_client
from fastapi.testclient import TestClient

from controlstudio_host.app import create_app
from controlstudio_host.mcp_client import MCPToolError
from controlstudio_host.models import (
    ApprovalDecision,
    ChangeRiskLevel,
    ChangeTargetIdentity,
    MessageRole,
)
from controlstudio_host.native_projects import NativeProjectRepository
from controlstudio_host.store import HostStore

CRASHED_HOLDER_TOKEN_VALUE = "crashed-holder"


class NativeProjectMCP(FakeMCP):
    def __init__(
        self,
        root: Path,
        *,
        instances: list[dict] | None = None,
        substitute_field: str | None = None,
        fail_create: bool = False,
        escape_project_path: bool = False,
    ) -> None:
        super().__init__()
        self.root = root
        self.instances = (
            [
                {
                    "pid": 101,
                    "hwnd": 202,
                    "title": "XG5000",
                    "class_name": "Afx:main",
                    "exe": "C:\\XG5000\\XG5000.exe",
                    "visible": True,
                    "rect": [0, 0, 1200, 800],
                    "diagnostics": {},
                }
            ]
            if instances is None
            else instances
        )
        self.substitute_field = substitute_field
        self.fail_create = fail_create
        self.escape_project_path = escape_project_path
        self.create_calls: list[dict] = []
        self.captured_workspaces: list[dict] = []
        self.current_project_path: Path | None = None

    async def call_read_tool(self, name: str, arguments: dict) -> dict:
        if name == "xg5000.instances":
            assert arguments == {}
            return {"instances": self.instances}
        if name == "project.list_programs":
            return {
                "ok": True,
                "verified": True,
                "programs": [
                    {
                        "program_id": "ld:main",
                        "name": "MAIN",
                        "language": "LD",
                    }
                ],
            }
        result = await super().call_read_tool(name, arguments)
        if name == "xg5000.attach":
            if (
                "project_path" not in arguments
                and self.current_project_path is not None
            ):
                result["project_path"] = str(self.current_project_path.resolve())
                result["project_hash"] = hashlib.sha256(
                    self.current_project_path.read_bytes()
                ).hexdigest()
            result["metadata"][
                "project_identity_verified_against_selected_pid"
            ] = True
        return result

    def sign_mutation_authorization(self, claims: dict) -> dict:
        return {**claims, "signature": "a" * 64}

    async def call_workspace_tool(self, name: str, arguments: dict) -> dict:
        if name == "project.create_native":
            self.create_calls.append(arguments)
            if self.fail_create:
                raise MCPToolError("project.create_native was rejected by MCP")
            destination = Path(arguments["destination_directory"])
            project = (
                self.root / f"{arguments['project_name']}.xgwx"
                if self.escape_project_path
                else destination / f"{arguments['project_name']}.xgwx"
            )
            project.write_bytes(b"native-xg5000-project")
            digest = hashlib.sha256(project.read_bytes()).hexdigest()
            data = {
                "project_name": arguments["project_name"],
                "project_path": str(project.resolve()),
                "project_sha256": digest,
                "project_size": project.stat().st_size,
                "destination_directory": str(destination.resolve()),
                "plc_series": arguments["plc_series"],
                "cpu_type": arguments["cpu_type"],
                "program_name": arguments["program_name"],
                "program_language": arguments["program_language"],
                "description": arguments["description"],
                "target": {
                    "owner_pid": arguments["pid"],
                    "project_open": False,
                },
                "identity": {
                    "verified": True,
                    "project_path": str(project.resolve()),
                    "window_title": f"XG5000 - {project.name}",
                },
                "controller_connected": False,
                "plc_write_performed": False,
                "plan_digest": arguments["authorization"]["plan_digest"],
                "authorization": {
                    "authorization_id": arguments["authorization"][
                        "authorization_id"
                    ],
                    "actor_id": arguments["authorization"]["actor_id"],
                    "plan_digest": arguments["authorization"]["plan_digest"],
                    "issued_at": arguments["authorization"]["issued_at"],
                    "expires_at": arguments["authorization"]["expires_at"],
                    "nonce": arguments["authorization"]["nonce"],
                    "signature_verified": True,
                    "consumed": True,
                },
            }
            if self.substitute_field is not None:
                data[self.substitute_field] = "SUBSTITUTED"
            return {
                "ok": True,
                "verified": True,
                "operation": "project.create_native",
                "pid": arguments["pid"],
                "hwnd": arguments["hwnd"],
                "data": data,
                "warnings": [],
            }
        if name == "workspace.open_native":
            source = Path(arguments["source_path"])
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            assert digest == arguments["expected_source_hash"]
            workspace_id = str(uuid.uuid4())
            revision_id = str(uuid.uuid4())
            workspace_root = self.root / "captured" / workspace_id
            captured = workspace_root / "source" / source.name
            working = workspace_root / "working" / source.name
            captured.parent.mkdir(parents=True)
            working.parent.mkdir(parents=True)
            shutil.copy2(source, captured)
            shutil.copy2(source, working)
            result = {
                "ok": True,
                "verified": True,
                "workspace_id": workspace_id,
                "revision_id": revision_id,
                "source_kind": "local_file",
                "vendor": "ls",
                "engineering_system": "XG5000",
                "created_at": "2026-07-29T12:00:00+00:00",
                "source": {
                    "original_path": str(source.resolve()),
                    "captured_path": str(captured.resolve()),
                    "sha256": digest,
                    "original_state": "unchanged",
                    "original_current_sha256": digest,
                },
                "working_copy": {
                    "path": str(working.resolve()),
                    "baseline_sha256": digest,
                    "current_sha256": digest,
                    "state": "clean",
                },
            }
            self.captured_workspaces.append(result)
            return result
        return await super().call_workspace_tool(name, arguments)


class BlockingWorkspaceMCP(NativeProjectMCP):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.workspace_entered = threading.Event()
        self.workspace_release = threading.Event()

    async def call_workspace_tool(self, name: str, arguments: dict) -> dict:
        if name == "workspace.open_native":
            self.workspace_entered.set()
            released = await asyncio.to_thread(self.workspace_release.wait, 5.0)
            assert released
        return await super().call_workspace_tool(name, arguments)


def _reviewed_plan_binding(store: HostStore) -> dict:
    conversation = store.create_conversation(
        title="MachineA 계획",
        workflow="new_project_planning",
        project_brief={
            "project_name": "MachineA",
            "machine_purpose": "테스트 설비",
            "sequence": "시작, 완료",
            "hardware_and_io": "LS PLC",
            "safety_and_interlocks": "비상정지",
            "operating_modes": "자동, 수동",
            "performance_and_acceptance": "기본 동작 시험",
        },
    )
    artifact = {
        "schemaVersion": "1",
        "projectName": "MachineA",
        "status": "ready_for_review",
        "summary": "MachineA 기술 계획",
        "objective": "테스트 설비 생성",
        "sections": [
            {
                "sectionId": "project-brief",
                "title": "프로젝트 브리프",
                "items": [],
            },
            {
                "sectionId": "validation-plan",
                "title": "검증 계획",
                "items": [],
            },
        ],
        "openDecisions": [],
        "completeness": {
            "totalItems": 0,
            "confirmed": 0,
            "proposed": 0,
            "unknown": 0,
            "conflicting": 0,
            "notApplicable": 0,
            "blocking": 0,
        },
    }
    digest = hashlib.sha256(
        json.dumps(
            artifact,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    message = store.append_message(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="MachineA 계획",
        metadata={
            "workflow": "new_project_planning",
            "plan_status": "ready_for_review",
            "plan_artifact": artifact,
            "plan_digest": digest,
        },
    )
    assert message is not None
    section_ids = ["project-brief", "validation-plan"]
    store.review_project_plan(
        conversation_id=conversation.id,
        message_id=message.id,
        plan_digest=digest,
        reviewed_section_ids=section_ids,
        actor_id="browser:test",
    )
    return {
        "conversationId": conversation.id,
        "messageId": message.id,
        "planDigest": digest,
        "reviewedSectionIds": section_ids,
    }


def _request(
    parent: Path,
    store: HostStore,
    *,
    operation_id: str | None = None,
) -> dict:
    return {
        "operationId": operation_id or str(uuid.uuid4()),
        "projectName": "MachineA",
        "workspaceParent": str(parent.resolve()),
        "plcSeries": "XGK",
        "cpuModel": "XGK-CPUE",
        "programName": "MAIN",
        "language": "LD",
        "description": "Created by ControlStudio",
        "xg5000Pid": 101,
        "xg5000Hwnd": 202,
        "reviewedPlan": _reviewed_plan_binding(store),
    }


def _client(host_config, store: HostStore, mcp: FakeMCP) -> TestClient:
    client = TestClient(
        create_app(host_config, store=store, mcp=mcp),
        base_url="http://localhost:8765",
    )
    client.__enter__()
    authenticate_client(client, host_config)
    return client


def test_concurrent_target_requests_allow_only_one_xg5000_operation(
    tmp_path: Path,
    host_config,
) -> None:
    source = tmp_path / "existing.xgwx"
    source.write_bytes(b"existing-native-project")
    mcp = BlockingWorkspaceMCP(tmp_path)
    client = _client(
        host_config,
        HostStore(host_config.database_path),
        mcp,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            opening = executor.submit(
                client.post,
                "/api/v1/workspaces/open-local",
                json={
                    "sourcePath": str(source),
                    "expectedSourceHash": hashlib.sha256(
                        source.read_bytes()
                    ).hexdigest(),
                    "openInXg5000": False,
                    "timeoutSeconds": 10,
                },
            )
            assert mcp.workspace_entered.wait(2.0)
            blocked = client.post(
                "/api/v1/xg5000/attach",
                json={"pid": 101, "hwnd": 202},
            )
            assert blocked.status_code == 409
            assert "serialized" in blocked.json()["detail"]
            mcp.workspace_release.set()
            assert opening.result(timeout=5.0).status_code == 200
    finally:
        mcp.workspace_release.set()
        client.__exit__(None, None, None)


def test_native_project_success_is_verified_captured_and_idempotent(
    tmp_path: Path,
    host_config,
) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    store = HostStore(host_config.database_path)
    mcp = NativeProjectMCP(tmp_path)
    body = _request(parent, store)
    client = _client(host_config, store, mcp)
    try:
        created = client.post(
            "/api/v1/native-projects",
            json=body,
            headers={"Idempotency-Key": body["operationId"]},
        )
        assert created.status_code == 201
        payload = created.json()
        assert payload["created"] is True
        operation = payload["operation"]
        assert operation["nativeProjectId"] == body["operationId"]
        assert operation["operationId"] == body["operationId"]
        assert operation["state"] == "completed"
        assert operation["destinationDirectory"] == str(
            Path(operation["result"]["projectPath"]).parent
        )
        assert operation["result"]["identityVerified"] is True
        assert operation["result"]["controllerConnected"] is False
        assert operation["result"]["plcWritePerformed"] is False
        assert operation["request"]["reviewedPlan"] == body["reviewedPlan"]
        assert operation["result"]["workspaceBound"] is False
        assert (
            operation["result"]["workspaceRegistration"]["state"]
            == "captured_not_bound"
        )
        project = Path(operation["result"]["projectPath"])
        assert project.is_file()
        assert hashlib.sha256(project.read_bytes()).hexdigest() == operation[
            "result"
        ]["projectSha256"]
        assert len(mcp.create_calls) == 1

        replay = client.post(
            "/api/v1/native-projects",
            json=body,
            headers={"Idempotency-Key": body["operationId"]},
        )
        assert replay.status_code == 200
        assert replay.json()["created"] is False
        assert replay.json()["operation"] == operation
        assert len(mcp.create_calls) == 1

        loaded = client.get(
            f"/api/v1/native-projects/{body['operationId']}"
        )
        assert loaded.status_code == 200
        assert loaded.json() == operation
        status_payload = client.get("/api/v1/status").json()
        assert "native_project_create" not in status_payload["capabilities"]
        native_feature = next(
            item
            for item in status_payload["featureReadiness"]
            if item["featureId"] == "native_project_create"
        )
        assert native_feature["maturity"] == "preview"
        assert native_feature["verification"] == "fake_only"
        assert native_feature["currentReady"] is False
        assert status_payload["workspace"] is None
    finally:
        client.__exit__(None, None, None)


@pytest.mark.parametrize(
    "failure_kind",
    ["missing_review", "stale", "digest", "section_scope"],
)
def test_native_project_requires_exact_latest_fully_reviewed_plan(
    tmp_path: Path,
    host_config,
    failure_kind: str,
) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    store = HostStore(host_config.database_path)
    body = _request(parent, store)
    reviewed_plan = body["reviewedPlan"]
    if failure_kind == "missing_review":
        with sqlite3.connect(store.database_path) as connection:
            connection.execute(
                "DELETE FROM project_plan_reviews WHERE message_id = ?",
                (reviewed_plan["messageId"],),
            )
    elif failure_kind == "stale":
        appended = store.append_message(
            conversation_id=reviewed_plan["conversationId"],
            role=MessageRole.USER,
            content="기술 요구사항을 변경합니다.",
            metadata={"workflow": "new_project_planning"},
        )
        assert appended is not None
    elif failure_kind == "digest":
        body["reviewedPlan"] = {
            **reviewed_plan,
            "planDigest": "0" * 64,
        }
    else:
        body["reviewedPlan"] = {
            **reviewed_plan,
            "reviewedSectionIds": ["project-brief"],
        }

    mcp = NativeProjectMCP(tmp_path)
    client = _client(host_config, store, mcp)
    try:
        response = client.post(
            "/api/v1/native-projects",
            json=body,
            headers={"Idempotency-Key": body["operationId"]},
        )
        assert response.status_code == 409
        assert not mcp.create_calls
        assert list(parent.iterdir()) == []
    finally:
        client.__exit__(None, None, None)


def test_quarantined_native_project_can_reconcile_from_current_exact_evidence(
    tmp_path: Path,
    host_config,
) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    store = HostStore(host_config.database_path)
    mcp = NativeProjectMCP(tmp_path, fail_create=True)
    body = _request(parent, store)
    client = _client(host_config, store, mcp)
    try:
        created = client.post(
            "/api/v1/native-projects",
            json=body,
            headers={"Idempotency-Key": body["operationId"]},
        )
        assert created.status_code == 201
        operation = created.json()["operation"]
        assert operation["state"] == "quarantined"

        project = (
            Path(operation["destinationDirectory"])
            / f"{body['projectName']}.xgwx"
        )
        project.write_bytes(b"native-project-recovered-after-response-loss")
        recovered = client.post(
            f"/api/v1/native-projects/{operation['nativeProjectId']}/reconcile",
            json={
                "pid": body["xg5000Pid"],
                "hwnd": body["xg5000Hwnd"],
                "expectedProjectPath": str(project),
            },
        )
        assert recovered.status_code == 200
        payload = recovered.json()
        assert payload["reconciled"] is True
        assert payload["operation"]["state"] == "completed"
        assert payload["operation"]["result"]["identityVerified"] is True
        assert payload["operation"]["result"]["projectSha256"] == hashlib.sha256(
            project.read_bytes()
        ).hexdigest()
        assert store.active_target_operation_lease() is None
    finally:
        client.__exit__(None, None, None)


def test_native_project_reconcile_mismatch_stays_quarantined_and_locked(
    tmp_path: Path,
    host_config,
) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    store = HostStore(host_config.database_path)
    mcp = NativeProjectMCP(tmp_path, fail_create=True)
    body = _request(parent, store)
    client = _client(host_config, store, mcp)
    try:
        created = client.post(
            "/api/v1/native-projects",
            json=body,
            headers={"Idempotency-Key": body["operationId"]},
        )
        operation = created.json()["operation"]
        expected = (
            Path(operation["destinationDirectory"])
            / f"{body['projectName']}.xgwx"
        )
        response = client.post(
            f"/api/v1/native-projects/{operation['nativeProjectId']}/reconcile",
            json={
                "pid": body["xg5000Pid"] + 1,
                "hwnd": body["xg5000Hwnd"],
                "expectedProjectPath": str(expected),
            },
        )
        assert response.status_code == 200
        assert response.json()["reconciled"] is False
        assert response.json()["operation"]["state"] == "quarantined"
        lease = store.active_target_operation_lease()
        assert lease is not None
        assert lease["state"] == "STALE_BLOCKED"
    finally:
        client.__exit__(None, None, None)


def test_native_project_reconcile_confirms_not_created_only_from_other_project(
    tmp_path: Path,
    host_config,
) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    other_project = tmp_path / "existing-machine.xgwx"
    other_project.write_bytes(b"existing-machine-project")
    store = HostStore(host_config.database_path)
    mcp = NativeProjectMCP(tmp_path, fail_create=True)
    mcp.current_project_path = other_project
    body = _request(parent, store)
    client = _client(host_config, store, mcp)
    try:
        created = client.post(
            "/api/v1/native-projects",
            json=body,
            headers={"Idempotency-Key": body["operationId"]},
        )
        operation = created.json()["operation"]
        expected = (
            Path(operation["destinationDirectory"])
            / f"{body['projectName']}.xgwx"
        )
        assert not expected.exists()

        response = client.post(
            f"/api/v1/native-projects/{operation['nativeProjectId']}/reconcile",
            json={
                "pid": body["xg5000Pid"],
                "hwnd": body["xg5000Hwnd"],
                "expectedProjectPath": str(expected),
            },
        )
        assert response.status_code == 200
        assert response.json()["reconciled"] is True
        assert response.json()["operation"]["state"] == "failed"
        assert (
            response.json()["operation"]["errorCode"]
            == "NATIVE_PROJECT_CONFIRMED_NOT_CREATED"
        )
        assert store.active_target_operation_lease() is None
    finally:
        client.__exit__(None, None, None)


def test_native_project_replay_rejects_request_substitution(
    tmp_path: Path,
    host_config,
) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    mcp = NativeProjectMCP(tmp_path)
    store = HostStore(host_config.database_path)
    client = _client(host_config, store, mcp)
    body = _request(parent, store)
    try:
        first = client.post(
            "/api/v1/native-projects",
            json=body,
            headers={"Idempotency-Key": body["operationId"]},
        )
        assert first.status_code == 201
        changed = {**body, "cpuModel": "XGI-CPUU"}
        conflict = client.post(
            "/api/v1/native-projects",
            json=changed,
            headers={"Idempotency-Key": body["operationId"]},
        )
        assert conflict.status_code == 409
        assert len(mcp.create_calls) == 1
    finally:
        client.__exit__(None, None, None)


@pytest.mark.parametrize(
    "instances",
    [
        [],
        [
            {
                "pid": 101,
                "hwnd": 999,
                "visible": True,
            }
        ],
        [
            {"pid": 101, "hwnd": 202, "visible": True},
            {"pid": 101, "hwnd": 202, "visible": True},
        ],
    ],
)
def test_native_project_requires_one_exact_xg5000_instance(
    tmp_path: Path,
    host_config,
    instances: list[dict],
) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    store = HostStore(host_config.database_path)
    body = _request(parent, store)
    mcp = NativeProjectMCP(tmp_path, instances=instances)
    client = _client(host_config, store, mcp)
    try:
        response = client.post(
            "/api/v1/native-projects",
            json=body,
            headers={"Idempotency-Key": body["operationId"]},
        )
        assert response.status_code == 409
        assert not mcp.create_calls
        assert list(parent.iterdir()) == []
    finally:
        client.__exit__(None, None, None)


def test_native_project_malicious_mcp_substitution_is_quarantined_not_retried(
    tmp_path: Path,
    host_config,
) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    store = HostStore(host_config.database_path)
    body = _request(parent, store)
    mcp = NativeProjectMCP(tmp_path, substitute_field="cpu_type")
    client = _client(host_config, store, mcp)
    try:
        response = client.post(
            "/api/v1/native-projects",
            json=body,
            headers={"Idempotency-Key": body["operationId"]},
        )
        assert response.status_code == 201
        operation = response.json()["operation"]
        assert operation["state"] == "quarantined"
        assert operation["errorCode"] == "NATIVE_PROJECT_EVIDENCE_UNVERIFIED"
        assert len(mcp.create_calls) == 1

        replay = client.post(
            "/api/v1/native-projects",
            json=body,
            headers={"Idempotency-Key": body["operationId"]},
        )
        assert replay.status_code == 200
        assert replay.json()["operation"]["state"] == "quarantined"
        assert len(mcp.create_calls) == 1
    finally:
        client.__exit__(None, None, None)


def test_native_project_rejects_mcp_artifact_outside_allocated_directory(
    tmp_path: Path,
    host_config,
) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    store = HostStore(host_config.database_path)
    body = _request(parent, store)
    mcp = NativeProjectMCP(tmp_path, escape_project_path=True)
    client = _client(host_config, store, mcp)
    try:
        response = client.post(
            "/api/v1/native-projects",
            json=body,
            headers={"Idempotency-Key": body["operationId"]},
        )
        assert response.status_code == 201
        operation = response.json()["operation"]
        assert operation["state"] == "quarantined"
        assert "escaped" in operation["errorDetail"]
        assert len(mcp.create_calls) == 1
    finally:
        client.__exit__(None, None, None)


def test_native_project_mcp_failure_is_quarantined_and_blocks_target_switch(
    tmp_path: Path,
    host_config,
) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    store = HostStore(host_config.database_path)
    body = _request(parent, store)
    mcp = NativeProjectMCP(tmp_path, fail_create=True)
    client = _client(host_config, store, mcp)
    try:
        response = client.post(
            "/api/v1/native-projects",
            json=body,
            headers={"Idempotency-Key": body["operationId"]},
        )
        assert response.status_code == 201
        assert response.json()["operation"]["state"] == "quarantined"

        attach = client.post(
            "/api/v1/xg5000/attach",
            json={"pid": 101, "hwnd": 202},
        )
        assert attach.status_code == 409
    finally:
        client.__exit__(None, None, None)


def test_native_project_rejects_unsafe_parent_and_idempotency_header(
    tmp_path: Path,
    host_config,
) -> None:
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("unsafe", encoding="utf-8")
    store = HostStore(host_config.database_path)
    body = _request(parent_file, store)
    mcp = NativeProjectMCP(tmp_path)
    client = _client(host_config, store, mcp)
    try:
        missing_key = client.post("/api/v1/native-projects", json=body)
        assert missing_key.status_code == 409
        unsafe = client.post(
            "/api/v1/native-projects",
            json=body,
            headers={"Idempotency-Key": body["operationId"]},
        )
        assert unsafe.status_code == 409
        assert not mcp.create_calls
    finally:
        client.__exit__(None, None, None)


def test_native_project_rejects_existing_change_mutation_lease(
    tmp_path: Path,
    host_config,
) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    store = HostStore(host_config.database_path)
    target = ChangeTargetIdentity(
        workspace_id=str(uuid.uuid4()),
        revision_id=str(uuid.uuid4()),
        artifact_hash="a" * 64,
        source_artifact_hash="a" * 64,
        project_path=str(tmp_path / "existing.xgwx"),
        vendor="ls",
        engineering_system="XG5000",
    )
    transaction, _ = store.create_change_transaction(
        operation_id="change-lease-test",
        target=target,
        mutation_scope=f"workspace:{target.workspace_id}",
        risk_level=ChangeRiskLevel.LOW,
        intent="hold mutation lease",
        plan={"target": {}, "current": {}, "proposed": {}, "rollback": ["restore"]},
        plan_digest="b" * 64,
        requested_by_actor_id="test",
        approval_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    store.decide_change_transaction(
        transaction_id=transaction.id,
        decision=ApprovalDecision.APPROVE,
        expected_plan_digest=transaction.plan_digest,
        actor_id="test",
    )
    store.begin_change_execution(
        transaction_id=transaction.id,
        expected_plan_digest=transaction.plan_digest,
    )
    mcp = NativeProjectMCP(tmp_path)
    client = _client(host_config, store, mcp)
    body = _request(parent, store)
    try:
        response = client.post(
            "/api/v1/native-projects",
            json=body,
            headers={"Idempotency-Key": body["operationId"]},
        )
        assert response.status_code == 409
        assert not mcp.create_calls
    finally:
        client.__exit__(None, None, None)


def test_interrupted_native_project_is_quarantined_on_restart(tmp_path: Path) -> None:
    repository = NativeProjectRepository(tmp_path / "host.sqlite3")
    operation_id = str(uuid.uuid4())
    operation, created = repository.allocate(
        operation_id=operation_id,
        request_digest="a" * 64,
        actor_id="browser:test",
        request={"operationId": operation_id},
        destination_directory=str(tmp_path / "allocated"),
    )
    assert created is True
    repository.set_plan_digest(operation.id, "b" * 64)
    assert repository.begin_execution(operation.id) is True

    restarted = NativeProjectRepository(tmp_path / "host.sqlite3")
    assert restarted.reconcile_interrupted() == 1
    recovered = restarted.get(operation.id)
    assert recovered is not None
    assert recovered.state == "QUARANTINED"
    assert (
        recovered.error_code
        == "HOST_RESTARTED_DURING_NATIVE_PROJECT_CREATION"
    )


def test_restart_fails_pre_dispatch_native_allocation_and_releases_target(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "host.sqlite3"
    store = HostStore(database_path)
    repository = NativeProjectRepository(database_path)
    operation_id = str(uuid.uuid4())
    store.acquire_target_operation_lease(
        operation_id=operation_id,
        owner_kind="native_project_create",
        holder_token=CRASHED_HOLDER_TOKEN_VALUE,
        target_identity={"operationId": operation_id},
    )
    operation, _ = repository.allocate(
        operation_id=operation_id,
        request_digest="a" * 64,
        actor_id="browser:test",
        request={"operationId": operation_id},
        destination_directory=str(tmp_path / "allocated"),
    )

    restarted = HostStore(database_path)
    assert restarted.mark_previous_instance_target_leases_stale() == 1
    assert repository.reconcile_interrupted() == 1
    assert restarted.release_terminal_native_target_operation_leases() == 1
    recovered = repository.get(operation.id)
    assert recovered is not None
    assert recovered.state == "FAILED"
    assert (
        recovered.error_code
        == "HOST_RESTARTED_BEFORE_NATIVE_PROJECT_EXECUTION"
    )
    assert restarted.active_target_operation_lease() is None


def test_host_shutdown_is_rejected_during_native_project_ui_execution(
    tmp_path: Path,
    host_config,
) -> None:
    store = HostStore(host_config.database_path)
    repository = NativeProjectRepository(host_config.database_path)
    client = _client(host_config, store, NativeProjectMCP(tmp_path))
    try:
        operation_id = str(uuid.uuid4())
        operation, _ = repository.allocate(
            operation_id=operation_id,
            request_digest="a" * 64,
            actor_id="browser:test",
            request={"operationId": operation_id},
            destination_directory=str(tmp_path / "allocated"),
        )
        repository.set_plan_digest(operation.id, "b" * 64)
        assert repository.begin_execution(operation.id) is True
        client.app.state.request_shutdown = lambda: None

        response = client.post("/api/v1/host/shutdown")
        assert response.status_code == 409
        assert "native XG5000 project creation" in response.json()["detail"]
        assert client.app.state.shutdown_event.is_set() is False
    finally:
        client.__exit__(None, None, None)
