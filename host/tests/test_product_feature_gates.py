from __future__ import annotations

import uuid
from pathlib import Path

from conftest import FakeMCP, authenticate_client
from fastapi.testclient import TestClient

from controlstudio_host.app import create_app
from controlstudio_host.config import HostConfig

TEST_LAUNCH_TOKEN_VALUE = "test-launch-token-that-is-long-enough"


def _product_client(tmp_path: Path) -> tuple[TestClient, HostConfig, FakeMCP]:
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-web-dist",
        event_poll_interval_seconds=0.01,
        event_heartbeat_seconds=0.05,
        launch_token=TEST_LAUNCH_TOKEN_VALUE,
    )
    mcp = FakeMCP()
    client = TestClient(
        create_app(config, mcp=mcp),
        base_url="http://localhost:8765",
    )
    client.__enter__()
    authenticate_client(client, config)
    return client, config, mcp


def test_fake_only_native_create_is_blocked_in_normal_product_host(
    tmp_path: Path,
) -> None:
    client, _config, mcp = _product_client(tmp_path)
    operation_id = str(uuid.uuid4())
    try:
        response = client.post(
            "/api/v1/native-projects",
            headers={"Idempotency-Key": operation_id},
            json={
                "operationId": operation_id,
                "projectName": "MachineA",
                "workspaceParent": str(tmp_path),
                "plcSeries": "XGK",
                "cpuModel": "XGK-CPUE",
                "programName": "MAIN",
                "language": "LD",
                "description": "must not execute",
                "xg5000Pid": 101,
                "xg5000Hwnd": 202,
                "reviewedPlan": {
                    "conversationId": "planning-conversation",
                    "messageId": "planning-message",
                    "planDigest": "a" * 64,
                    "reviewedSectionIds": ["project-brief"],
                },
            },
        )

        assert response.status_code == 409
        assert "fake-backend evidence only" in response.json()["detail"]
        assert mcp.controller_calls == []
        assert client.app.state.native_project_repository.get(operation_id) is None
    finally:
        client.__exit__(None, None, None)


def test_fake_only_online_observe_is_blocked_but_disconnect_remains_safe(
    tmp_path: Path,
) -> None:
    client, _config, mcp = _product_client(tmp_path)
    try:
        connected = client.post(
            "/api/v1/controllers/ls/connect",
            json={
                "host": "100.64.12.34",
                "port": 2004,
                "timeoutSeconds": 1.5,
            },
        )
        status_response = client.get("/api/v1/controllers/ls/status")
        read = client.post(
            "/api/v1/controllers/ls/read",
            json={"dataType": "word", "variables": ["%MW100"]},
        )
        disconnected = client.delete("/api/v1/controllers/ls/connection")

        assert connected.status_code == 409
        assert status_response.status_code == 409
        assert read.status_code == 409
        assert disconnected.status_code == 204
        assert mcp.controller_calls == []
        status_payload = client.get("/api/v1/status").json()
        feature = next(
            item
            for item in status_payload["featureReadiness"]
            if item["featureId"] == "online_observe"
        )
        assert feature["currentReady"] is False
        assert "online_observe" not in status_payload["capabilities"]
    finally:
        client.__exit__(None, None, None)


def test_executable_change_transaction_requires_current_verified_session(
    tmp_path: Path,
) -> None:
    client, _config, _mcp = _product_client(tmp_path)
    try:
        opened = client.post(
            "/api/v1/workspaces/open-local",
            json={
                "sourcePath": str(tmp_path / "machine.xgwx"),
                "openInXg5000": False,
            },
        )
        assert opened.status_code == 200
        status_payload = client.get("/api/v1/status").json()
        feature = next(
            item
            for item in status_payload["featureReadiness"]
            if item["featureId"] == "offline_mutate"
        )
        assert feature["currentReady"] is False
        assert "offline_mutate" not in status_payload["capabilities"]

        created = client.post(
            "/api/v1/change-transactions",
            json={
                "operationId": "change-not-ready-0001",
                "intent": "must not become executable",
                "riskLevel": "high",
                "plan": {
                    "target": {
                        "programId": "program:MAIN",
                        "tokenOffset": 14,
                    },
                    "current": {"value": "M0001"},
                    "proposed": {"value": "M0002"},
                    "impact": [],
                    "risks": [],
                    "verificationScope": "offline_native_check_only",
                    "validation": [
                        "structural_diff",
                        "xg5000_program_check",
                        "project_save",
                    ],
                    "manualFollowUp": [],
                    "rollback": ["Restore the exact backup"],
                },
                "approvalTtlSeconds": 900,
            },
        )

        assert created.status_code == 409
        assert "verified engineering session is required" in created.json()["detail"]
        assert client.get("/api/v1/change-transactions").json() == []
    finally:
        client.__exit__(None, None, None)
