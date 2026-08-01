from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from controlstudio_contracts import AgentToolDefinitionV1

from controlstudio_host.agent_client import (
    AgentSidecarCrashed,
    AgentSupervisor,
    SubprocessAgentClient,
    _sidecar_environment,
)
from controlstudio_host.agent_protocol import (
    AgentProtocolError,
    AgentProtocolMessage,
    assert_agent_safe_payload,
    decode_message,
    encode_message,
    scrub_agent_tool_result,
)
from controlstudio_host.model_connection import ModelSecrets, StoredModelConnection
from controlstudio_host.models import Message, MessageRole

TEST_MODEL_API_KEY_VALUE = "test-key"


def test_agent_protocol_rejects_host_target_identity() -> None:
    with pytest.raises(AgentProtocolError):
        assert_agent_safe_payload(
            {
                "trusted_context": {
                    "engineering_session": {"project_path": r"C:\projects\line.xgwx"}
                }
            }
        )


def test_agent_protocol_round_trip_is_versioned_and_strict() -> None:
    frame = decode_message(encode_message("health", {"request_id": "request-1"}))
    assert frame.type == "health"
    assert frame.payload == {"request_id": "request-1"}
    with pytest.raises(AgentProtocolError):
        decode_message(b'{"protocol_version":"2.0","type":"health","payload":{}}\n')


def test_agent_tool_result_does_not_echo_host_target_identity() -> None:
    assert scrub_agent_tool_result(
        {
            "session_id": "session-1",
            "pid": 10,
            "project_path": r"C:\projects\line.xgwx",
            "status": {"host": "10.1.2.3", "mode": "RUN"},
        }
    ) == {"status": {"mode": "RUN"}}


def test_sidecar_environment_excludes_host_approval_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTROLSTUDIO_MCP_AUTHORIZATION_SECRET", "must-not-inherit")
    monkeypatch.setenv("CONTROLSTUDIO_APPROVAL_SECRET", "must-not-inherit")
    environment = _sidecar_environment()
    assert "CONTROLSTUDIO_MCP_AUTHORIZATION_SECRET" not in environment
    assert "CONTROLSTUDIO_APPROVAL_SECRET" not in environment


@pytest.mark.anyio
async def test_sidecar_handshake_health_and_safe_shutdown() -> None:
    # The public Host does not ship the private Agent artifact.  Contract tests
    # explicitly inject the sidecar command instead of relying on a Platform
    # package import.
    supervisor = AgentSupervisor(
        command=(
            sys.executable,
            str(Path(__file__).with_name("fixtures") / "mock_agent_sidecar.py"),
        ),
        startup_timeout_seconds=20,
    )
    try:
        await supervisor.ensure_started()
        assert supervisor.manifest_agent_id == "controlstudio-mock-agent"
        assert await supervisor.sync_knowledge(
            [{"reference": "knowledge://source/chunk", "content": "timer reference"}]
        ) == 1
    finally:
        await supervisor.stop()


@pytest.mark.anyio
async def test_default_supervisor_uses_monorepo_agent_console_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CONTROLSTUDIO_AGENT_COMMAND", raising=False)
    executable = (
        Path(__file__).resolve().parents[2]
        / "agent"
        / ".venv"
        / "Scripts"
        / "controlstudio-agent.exe"
    )
    supervisor = AgentSupervisor(startup_timeout_seconds=20)
    if not executable.is_file():
        assert supervisor._command == ("controlstudio-agent",)
        return

    assert supervisor._command == (str(executable),)
    try:
        await supervisor.ensure_started()
        assert supervisor.manifest_agent_id == "controlstudio-pydantic-ai-sidecar"
    finally:
        await supervisor.stop()


def test_supervisor_accepts_explicit_artifact_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CONTROLSTUDIO_AGENT_COMMAND",
        r'"C:\Program Files\ControlStudio\controlstudio-agent.exe" --stdio',
    )
    assert AgentSupervisor()._command == (
        r"C:\Program Files\ControlStudio\controlstudio-agent.exe",
        "--stdio",
    )

    monkeypatch.setenv(
        "CONTROLSTUDIO_AGENT_COMMAND",
        r"C:\ControlStudio\controlstudio-agent.exe --stdio",
    )
    assert AgentSupervisor()._command == (
        r"C:\ControlStudio\controlstudio-agent.exe",
        "--stdio",
    )


@pytest.mark.anyio
async def test_sidecar_startup_timeout_terminates_unresponsive_child() -> None:
    supervisor = AgentSupervisor(
        command=(sys.executable, "-c", "import time; time.sleep(30)"),
        startup_timeout_seconds=0.1,
        shutdown_timeout_seconds=0.1,
    )
    with pytest.raises(AgentSidecarCrashed):
        await supervisor.ensure_started()
    assert supervisor._process is None


@pytest.mark.anyio
async def test_host_client_executes_only_brokered_tool_and_scrubs_result() -> None:
    class Broker:
        async def agent_tool_catalog(self) -> list[AgentToolDefinitionV1]:
            return [
                AgentToolDefinitionV1(
                    tool_name="read_ladder_program",
                    description="Read a program.",
                    parameters_json_schema={"type": "object", "properties": {}},
                )
            ]

        async def execute_external_tool_request(self, request: Any, *, sink: Any) -> dict[str, Any]:
            assert request.tool_name == "read_ladder_program"
            await sink("tool.started", {"tool_name": "program.read_ld"})
            await sink("tool.completed", {"tool_name": "program.read_ld"})
            return {
                "pid": 77,
                "project_path": r"C:\projects\line.xgwx",
                "program": {"name": "MAIN"},
            }

    class ScriptedSupervisor:
        def __init__(self) -> None:
            self.payload: dict[str, Any] | None = None
            self.tool_results: list[dict[str, Any]] = []

        async def run(self, payload: dict[str, Any], *, on_message: Any) -> None:
            self.payload = payload
            await on_message(
                AgentProtocolMessage(
                    protocol_version="1.0",
                    type="tool.request",
                    payload={
                        "protocol_version": "1.0",
                        "request_id": "tool-request-1",
                        "conversation_id": "conversation-1",
                        "run_id": "run-1",
                        "tool_name": "read_ladder_program",
                        "tool_call_id": "call-1",
                        "arguments": {},
                    },
                )
            )
            await on_message(
                AgentProtocolMessage(
                    protocol_version="1.0",
                    type="run.final",
                    payload={
                        "run_id": "run-1",
                        "output": {
                            "content": "MAIN was read.",
                            "model": "test-model",
                            "input_tokens": 1,
                            "output_tokens": 2,
                            "workflow": "engineering",
                        },
                    },
                )
            )

        async def send_tool_result(self, payload: dict[str, Any]) -> None:
            self.tool_results.append(payload)

        async def cancel_run(self, run_id: str) -> None:
            del run_id

    settings = StoredModelConnection(
        protocol="openai_chat_completions",
        base_url="https://example.invalid/v1",
        model="test-model",
        timeout_seconds=10,
        secret_ref="test",
        updated_at=datetime.now(timezone.utc),
        capability_verified_at=None,
        secrets=ModelSecrets(api_key=TEST_MODEL_API_KEY_VALUE, custom_headers={}),
    )
    supervisor = ScriptedSupervisor()
    client = SubprocessAgentClient(settings, Broker(), supervisor)  # type: ignore[arg-type]
    events: list[str] = []

    async def on_event(event_type: str, payload: dict[str, Any]) -> None:
        del payload
        events.append(event_type)

    output = await client.run(
        prompt="Read MAIN",
        history=[
            Message(
                id="message-1",
                conversation_id="conversation-1",
                sequence=1,
                role=MessageRole.USER,
                content="Read MAIN",
                metadata={"project_path": r"C:\projects\line.xgwx"},
                created_at=datetime.now(timezone.utc),
            )
        ],
        conversation_id="conversation-1",
        run_id="run-1",
        trusted_context={
            "session_id": "must-not-cross-boundary",
            "pid": 77,
            "project_path": r"C:\projects\line.xgwx",
            "change_request_scope": "analysis_only",
        },
        on_tool_event=on_event,
    )

    assert output.content == "MAIN was read."
    assert events == ["tool.started", "tool.completed"]
    assert supervisor.payload is not None
    assert supervisor.payload["history"][0] == {
        "role": "user",
        "content": "Read MAIN",
        "created_at": supervisor.payload["history"][0]["created_at"],
    }
    assert supervisor.payload["trusted_context"] == {
        "workflow": "engineering",
        "change_request_scope": "analysis_only",
        "knowledge_search_available": False,
        "change_planner_available": False,
    }
    assert supervisor.tool_results == [
        {
            "request_id": "tool-request-1",
            "status": "completed",
            "result": {"program": {"name": "MAIN"}},
        }
    ]
