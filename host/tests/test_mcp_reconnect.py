from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from controlstudio_host.mcp_client import MCPGateway, MCPStatus


class _BrokenClient:
    def __init__(self) -> None:
        self.exit_calls = 0

    async def __aexit__(self, *_args: object) -> None:
        self.exit_calls += 1


class _RecoveryClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        self.calls.append((name, dict(arguments)))
        return type(
            "Result",
            (),
            {
                "is_error": False,
                "structured_content": {"status": "ok", "data": {"ok": True}},
                "data": None,
                "content": [],
            },
        )()


class MCPReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_unbound_terminal_recovery_status_reaches_transport(self) -> None:
        gateway = MCPGateway()
        client = _RecoveryClient()
        gateway._client = client  # type: ignore[assignment]
        gateway._status = MCPStatus(status="ready")

        result = await gateway.call_recovery_tool(
            "xg5000.session_status",
            {"session_id": "lost-memory-session"},
        )

        self.assertEqual(result, {"ok": True})
        self.assertIsNone(gateway.agent_session_identity_snapshot)
        self.assertEqual(
            client.calls,
            [("xg5000.session_status", {"session_id": "lost-memory-session"})],
        )

    async def test_transport_loss_replaces_client_once_without_host_restart(self) -> None:
        gateway = MCPGateway()
        broken = _BrokenClient()
        gateway._client = broken  # type: ignore[assignment]
        gateway._status = MCPStatus(status="ready")
        start = AsyncMock(return_value=MCPStatus(status="ready"))

        with patch.object(gateway, "start", start):
            gateway._mark_connection_lost(RuntimeError("transport closed"))
            first_task = gateway._reconnect_task
            gateway._mark_connection_lost(RuntimeError("same disconnect"))
            self.assertIs(gateway._reconnect_task, first_task)
            self.assertIsNotNone(first_task)
            await first_task

        self.assertEqual(broken.exit_calls, 1)
        start.assert_awaited_once_with()
        self.assertIsNone(gateway._reconnect_task)

    async def test_stop_cancels_pending_reconnect(self) -> None:
        gateway = MCPGateway()
        gateway._stopping = True
        gateway._mark_connection_lost(RuntimeError("shutdown race"))
        await asyncio.sleep(0)
        self.assertIsNone(gateway._reconnect_task)


if __name__ == "__main__":
    unittest.main()
