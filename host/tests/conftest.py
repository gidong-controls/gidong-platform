from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from controlstudio_host.agent_client import AgentSupervisor
from controlstudio_host.app import create_app
from controlstudio_host.config import HostConfig
from controlstudio_host.mcp_client import MCPStatus

TEST_LAUNCH_TOKEN_VALUE = "test-launch-token-that-is-long-enough"


class FakeMCP:
    def __init__(self) -> None:
        self.status = MCPStatus(
            status="ready",
            server_name="xg5000-mcp-server",
            server_version="0.1.0",
            protocol_version="2025-11-25",
            tool_count=34,
            agent_tool_count=12,
        )
        self.agent_toolset = object()
        self.bound_controller_endpoint: dict | None = None
        self.controller_calls: list[tuple[str, dict]] = []

    async def start(self) -> MCPStatus:
        return self.status

    async def stop(self) -> None:
        return None

    def bind_agent_session(self, session: dict) -> None:
        self.bound_session = dict(session)

    def clear_agent_session(self) -> None:
        self.bound_session = None

    def bind_controller_endpoint(
        self,
        endpoint: dict,
        *,
        observation_current: bool = False,
    ) -> None:
        self.bound_controller_endpoint = dict(endpoint)
        self.controller_observation_current = observation_current

    def clear_controller_endpoint(self) -> None:
        self.bound_controller_endpoint = None
        self.controller_observation_current = False

    async def call_controller_tool(
        self,
        name: str,
        arguments: dict,
        *,
        endpoint: dict | None = None,
    ) -> dict:
        selected = dict(endpoint or self.bound_controller_endpoint or {})
        if not selected:
            raise AssertionError("controller endpoint was not Host-bound")
        self.controller_calls.append((name, {**arguments, **selected}))
        response_endpoint = {
            "host": selected["host"],
            "port": selected["port"],
            "transport": "tcp",
            "protocol": "XGT_FENET",
        }
        if name == "controller.ls.status":
            return {
                "schema_version": "1.0",
                "operation": name,
                "endpoint": response_endpoint,
                "observedAt": "2026-07-29T12:00:00+00:00",
                "readOnly": True,
                "status": {
                    "mode": "RUN",
                    "stateFlags": ["RUN"],
                    "systemStateRaw": 1,
                    "padtConnectionFlag": 0,
                    "configurationError": 0,
                    "configurationWarning": 0,
                },
                "provisionalIdentity": {
                    "vendor": "LS ELECTRIC",
                    "protocol": "XGT_FENET",
                    "cpuFamily": "XGK",
                    "cpuInfoCode": 1,
                    "cpuTypeFlag": 2,
                    "osVersion": "1.2.3",
                    "module": {"base": 0, "slot": 0},
                    "fingerprintSha256": "f" * 64,
                    "evidenceStrength": "provisional_status_only",
                    "strongEnoughForWrite": False,
                    "writeAuthorizationReason": "read-only provisional identity",
                },
            }
        if name == "controller.ls.read":
            return {
                "schema_version": "1.0",
                "operation": name,
                "endpoint": response_endpoint,
                "observedAt": "2026-07-29T12:00:01+00:00",
                "dataType": arguments["data_type"],
                "values": [
                    {
                        "variable": variable,
                        "rawHex": "01",
                        "unsignedValue": 1,
                    }
                    for variable in arguments["variables"]
                ],
                "readOnly": True,
                "strongEnoughForWrite": False,
            }
        raise AssertionError(f"unexpected controller tool: {name}")

    async def call_read_tool(self, name: str, arguments: dict) -> dict:
        if name == "xg5000.instances":
            assert arguments == {}
            return {"instances": []}
        if name == "xg5000.attach":
            controlled_read_binding = bool(arguments.get("workspace_id"))
            return {
                "session_id": "session-test",
                "pid": arguments["pid"],
                "hwnd": arguments["hwnd"],
                "stage": "PROJECT_OPEN",
                "project_path": arguments.get(
                    "project_path", "C:\\projects\\machine.xgwx"
                ),
                "project_hash": arguments.get("project_hash", "a" * 64),
                "recovery": None,
                "metadata": {
                    "version": "4.70",
                    "project_identity_verified_against_selected_pid": False,
                    "project_identity_read_only_bound_to_selected_pid": (
                        controlled_read_binding
                    ),
                    "project_identity_assurance": (
                        "controlled_launch_read_only"
                        if controlled_read_binding
                        else "unverified"
                    ),
                },
            }
        raise AssertionError(f"unexpected tool: {name}")

    async def call_workspace_tool(self, name: str, arguments: dict) -> dict:
        if name == "workspace.open_native":
            return {
                "ok": True,
                "verified": True,
                "operation": name,
                "workspace_id": "11111111-1111-4111-8111-111111111111",
                "revision_id": "22222222-2222-4222-8222-222222222222",
                "source_kind": "local_file",
                "vendor": "ls",
                "engineering_system": "XG5000",
                "created_at": "2026-07-28T12:00:00+00:00",
                "source": {
                    "original_path": arguments["source_path"],
                    "captured_path": "C:\\workspaces\\source\\machine.xgwx",
                    "sha256": "a" * 64,
                    "original_state": "unchanged",
                    "original_current_sha256": "a" * 64,
                },
                "working_copy": {
                    "path": "C:\\workspaces\\working\\machine.xgwx",
                    "baseline_sha256": "a" * 64,
                    "current_sha256": "a" * 64,
                    "state": "clean",
                },
            }
        if name == "workspace.status":
            return await self.call_workspace_tool(
                "workspace.open_native",
                {"source_path": "C:\\projects\\machine.xgwx"},
            )
        if name == "xg5000.open_project":
            return {
                "ok": True,
                "verified": True,
                "operation": name,
                "workspace_id": arguments["workspace_id"],
                "working_path": "C:\\workspaces\\working\\machine.xgwx",
                "working_hash": arguments["expected_working_hash"],
                "launch_pid": 1234,
                "window_detected": True,
                "project_identity_verified_against_selected_pid": False,
                "project_identity_read_only_bound_to_selected_pid": True,
                "identity_assurance": "controlled_launch_read_only",
                "instance": {
                    "pid": 1234,
                    "hwnd": 5678,
                    "title": "XG5000 - machine.xgwx",
                },
                "observed_instances": [],
                "detail": "detected",
            }
        raise AssertionError(f"unexpected workspace tool: {name}")


def authenticate_client(test_client: TestClient, config: HostConfig) -> str:
    handoff = test_client.post(
        "/api/launcher/handoff",
        headers={
            "Authorization": f"Bearer {config.launch_token}",
        },
    )
    assert handoff.status_code == 201
    response = test_client.post(
        "/api/session",
        headers={
            "Authorization": f"Bearer {handoff.json()['handoffTicket']}",
        },
    )
    assert response.status_code == 200
    session_token = response.json()["sessionToken"]
    test_client.headers.update({"Authorization": f"Bearer {session_token}"})
    return session_token


@pytest.fixture
def host_config(tmp_path: Path) -> HostConfig:
    return HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-web-dist",
        event_poll_interval_seconds=0.01,
        event_heartbeat_seconds=0.05,
        launch_token=TEST_LAUNCH_TOKEN_VALUE,
        validation_enable_native_project_create=True,
        validation_enable_online_observe=True,
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def client(host_config: HostConfig) -> TestClient:
    fixture = Path(__file__).parent / "fixtures" / "mock_agent_sidecar.py"
    agent_supervisor = AgentSupervisor(command=(sys.executable, str(fixture)))
    with TestClient(
        create_app(host_config, mcp=FakeMCP(), agent_supervisor=agent_supervisor),
        base_url="http://localhost:8765",
    ) as test_client:
        authenticate_client(test_client, host_config)
        yield test_client
