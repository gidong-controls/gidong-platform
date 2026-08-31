from __future__ import annotations

import unittest
from typing import Any

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


if __name__ == "__main__":
    unittest.main()
