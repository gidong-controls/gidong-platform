from __future__ import annotations

import pytest
from conftest import FakeMCP, authenticate_client
from fastapi.testclient import TestClient

from controlstudio_host.app import create_app
from controlstudio_host.mcp_client import MCPToolError


@pytest.mark.parametrize(
    "host",
    [
        "plc.example",
        "8.8.8.8",
        "0.0.0.0",
        "224.0.0.1",
        "127.0.0.1",
        "169.254.10.20",
        "::1",
        "fe80::1",
    ],
)
def test_controller_connect_rejects_untrusted_endpoint(
    client: TestClient,
    host: str,
) -> None:
    response = client.post(
        "/api/v1/controllers/ls/connect",
        json={"host": host, "port": 2004, "timeoutSeconds": 2},
    )
    assert response.status_code == 422


def test_controller_connect_rejects_nonstandard_port(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/controllers/ls/connect",
        json={"host": "192.168.0.10", "port": 8080, "timeoutSeconds": 2},
    )
    assert response.status_code == 422


def test_controller_connect_status_read_and_disconnect_are_read_only(
    client: TestClient,
) -> None:
    initial = client.get("/api/v1/status")
    assert initial.status_code == 200
    assert initial.json()["controller"] == {
        "state": "unavailable",
        "detail": "온라인 LS PLC endpoint가 선택되지 않았습니다.",
        "version": None,
        "vendor": "ls",
        "protocol": "XGT_FENET",
        "endpoint": None,
        "timeoutSeconds": None,
        "readOnly": True,
        "strongEnoughForWrite": False,
        "observedAt": None,
        "provisionalIdentity": None,
    }

    connected = client.post(
        "/api/v1/controllers/ls/connect",
        json={"host": "100.64.12.34", "port": 2004, "timeoutSeconds": 1.5},
    )
    assert connected.status_code == 200
    connected_body = connected.json()
    assert connected_body["operation"] == "controller.ls.status"
    assert connected_body["endpoint"] == {
        "host": "100.64.12.34",
        "port": 2004,
        "transport": "tcp",
        "protocol": "XGT_FENET",
    }
    assert connected_body["readOnly"] is True
    assert (
        connected_body["provisionalIdentity"]["strongEnoughForWrite"] is False
    )

    status_response = client.get("/api/v1/controllers/ls/status")
    assert status_response.status_code == 200
    assert status_response.json()["status"]["mode"] == "RUN"

    read_response = client.post(
        "/api/v1/controllers/ls/read",
        json={"dataType": "word", "variables": ["%MW100", "%MW101"]},
    )
    assert read_response.status_code == 200
    read_body = read_response.json()
    assert read_body["dataType"] == "WORD"
    assert read_body["readOnly"] is True
    assert read_body["strongEnoughForWrite"] is False
    assert [item["variable"] for item in read_body["values"]] == [
        "%MW100",
        "%MW101",
    ]

    mcp = client.app.state.mcp
    assert mcp.controller_calls[-1] == (
        "controller.ls.read",
        {
            "data_type": "WORD",
            "variables": ["%MW100", "%MW101"],
            "host": "100.64.12.34",
            "port": 2004,
            "timeout_seconds": 1.5,
        },
    )

    endpoint_override = client.post(
        "/api/v1/controllers/ls/read",
        json={
            "dataType": "WORD",
            "variables": ["%MW100"],
            "host": "192.168.0.9",
        },
    )
    assert endpoint_override.status_code == 422

    disconnected = client.delete("/api/v1/controllers/ls/connection")
    assert disconnected.status_code == 204
    assert mcp.bound_controller_endpoint is None
    assert client.get("/api/v1/controllers/ls/status").status_code == 409


def test_failed_probe_does_not_replace_existing_selected_endpoint(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connected = client.post(
        "/api/v1/controllers/ls/connect",
        json={"host": "100.64.12.34"},
    )
    assert connected.status_code == 200
    mcp = client.app.state.mcp

    async def fail_probe(*_args, **_kwargs):
        raise MCPToolError("controller.ls.status failed: timeout")

    monkeypatch.setattr(mcp, "call_controller_tool", fail_probe)
    failed = client.post(
        "/api/v1/controllers/ls/connect",
        json={"host": "192.168.0.50"},
    )
    assert failed.status_code == 502
    assert mcp.bound_controller_endpoint["host"] == "100.64.12.34"
    persisted = client.app.state.store.get_host_state("active_ls_controller")
    assert persisted is not None
    assert persisted["endpoint"]["host"] == "100.64.12.34"


def test_selected_controller_endpoint_is_restored_from_server_store(
    host_config,
) -> None:
    first_mcp = FakeMCP()
    with TestClient(
        create_app(host_config, mcp=first_mcp),
        base_url="http://localhost:8765",
    ) as first:
        authenticate_client(first, host_config)
        response = first.post(
            "/api/v1/controllers/ls/connect",
            json={"host": "100.64.12.34", "port": 2004, "timeoutSeconds": 2},
        )
        assert response.status_code == 200

    second_mcp = FakeMCP()
    with TestClient(
        create_app(host_config, mcp=second_mcp),
        base_url="http://localhost:8765",
    ) as second:
        authenticate_client(second, host_config)
        assert second_mcp.bound_controller_endpoint == {
            "host": "100.64.12.34",
            "port": 2004,
            "timeout_seconds": 2.0,
        }
        assert second_mcp.controller_observation_current is False
        restored = second.get("/api/v1/status")
        assert restored.status_code == 200
        assert restored.json()["controller"]["state"] == "degraded"
        assert restored.json()["controller"]["observedAt"] is None
        refreshed = second.get("/api/v1/controllers/ls/status")
        assert refreshed.status_code == 200
        assert second_mcp.controller_observation_current is True
        restored = second.get("/api/v1/status")
        assert restored.json()["controller"]["state"] == "ready"
        assert (
            restored.json()["controller"]["strongEnoughForWrite"] is False
        )
