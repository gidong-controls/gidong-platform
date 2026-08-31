from __future__ import annotations

import unittest
from typing import Any, cast

from controlstudio_contracts import ExternalToolRequestV1

from controlstudio_host.app import _agent_tool_evidence_ref
from controlstudio_host.execution_broker import MCPExecutionBroker
from controlstudio_host.mcp_client import (
    AGENT_READ_ANALYZE_MCP_TOOLS,
    AGENT_TOOL_NAMES,
    AGENT_TOOL_REQUIRED_IDENTITY_FIELDS,
    HOST_CHANGE_MCP_TOOLS,
    SESSION_IDENTITY_FIELDS,
    MCPGateway,
    MCPToolError,
)


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        return {"ok": True}


class _SimulatorStatusGateway:
    def __init__(self) -> None:
        self._identity = {
            "session_id": "analysis-session",
            "pid": 101,
            "hwnd": 202,
            "project_path": "C:/work/project.xgwx",
            "project_hash": "a" * 64,
        }

    @property
    def agent_session_identity_snapshot(self) -> dict[str, Any]:
        return dict(self._identity)

    async def execute_agent_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name != "sim.target_status" or arguments:
            raise AssertionError("simulator status request changed")
        return {
            "ok": True,
            "verified": False,
            "operation": "sim.target_status",
            "read_only": True,
            "project_unchanged": True,
            "target_status": {
                "verified": False,
                "classification": "NOT_SIMULATOR_TARGET",
            },
            "actions": {
                "start": False,
                "write_program": False,
                "run": False,
                "stop": False,
            },
        }


class SimulatorTargetStatusAgentExposureTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_status_read_uses_the_bound_exact_session_identity(self) -> None:
        self.assertEqual(AGENT_TOOL_NAMES["get_simulator_target_status"], "sim.target_status")
        self.assertIn("sim.target_status", AGENT_READ_ANALYZE_MCP_TOOLS)
        self.assertNotIn("sim.target_status", HOST_CHANGE_MCP_TOOLS)
        self.assertEqual(
            AGENT_TOOL_REQUIRED_IDENTITY_FIELDS["sim.target_status"],
            SESSION_IDENTITY_FIELDS,
        )

        session = {
            "session_id": "analysis-session",
            "pid": 101,
            "hwnd": 202,
            "project_path": "C:/work/project.xgwx",
            "project_hash": "a" * 64,
        }
        gateway = MCPGateway(command="unused")
        gateway.bind_agent_session(session)
        client = _RecordingClient()

        result = await gateway.call_agent_tool(
            "sim.target_status",
            {},
            call_tool=client.call_tool,
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(client.calls, [("sim.target_status", session)])
        with self.assertRaisesRegex(MCPToolError, "stale or mismatched session identity: hwnd"):
            await gateway.call_agent_tool(
                "sim.target_status",
                {"hwnd": 888},
                call_tool=client.call_tool,
            )
        self.assertEqual(client.calls, [("sim.target_status", session)])

    async def test_status_classification_reaches_existing_evidence_summary(self) -> None:
        gateway = _SimulatorStatusGateway()
        broker = MCPExecutionBroker(cast(MCPGateway, gateway))
        events: list[tuple[str, dict[str, Any]]] = []

        async def capture(event_type: str, payload: dict[str, Any]) -> None:
            events.append((event_type, dict(payload)))

        await broker.execute_external_tool_request(
            ExternalToolRequestV1(
                request_id="sim-status-request",
                conversation_id="sim-status-conversation",
                run_id="sim-status-run",
                tool_name="get_simulator_target_status",
                arguments={},
            ),
            sink=capture,
        )

        self.assertEqual([kind for kind, _payload in events], ["tool.started", "tool.completed"])
        completed = events[-1][1]
        self.assertEqual(
            completed["simulator_target_status"],
            {"verified": False, "classification": "NOT_SIMULATOR_TARGET"},
        )
        evidence = _agent_tool_evidence_ref(
            conversation_id="sim-status-conversation",
            run_id="sim-status-run",
            tool_name="get_simulator_target_status",
            tool_call_id=None,
            simulator_target_status=completed["simulator_target_status"],
        )
        assert evidence is not None
        self.assertIn("검증되지는 않았습니다", evidence["summary"])
        self.assertIn("offline 판정이 아닙니다", evidence["summary"])


if __name__ == "__main__":
    unittest.main()
