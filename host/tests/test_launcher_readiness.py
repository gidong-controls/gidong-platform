from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from controlstudio_host.app import create_app
from controlstudio_host.config import HostConfig
from controlstudio_host.mcp_client import MCPGateway, MCPStatus

_CHECKOUT_ROOT = Path(__file__).resolve().parents[3]
_MCP_ROOT = _CHECKOUT_ROOT / "gidong-plc-mcp"
_MCP_COMMAND = _MCP_ROOT / ".venv" / "Scripts" / "controlstudio-plc-mcp.exe"


class _ReadinessMCP:
    """Small Host-lifecycle MCP double; bootstrap must not use a PLC tool."""

    def __init__(self) -> None:
        self.status = MCPStatus(
            status="ready",
            server_name="launcher-readiness-fixture",
            server_version="1.0.0",
            protocol_version="2025-11-25",
            tool_count=0,
            agent_tool_count=0,
        )
        self.controller_observation_current = False

    @property
    def agent_session_identity_snapshot(self):
        return None

    async def start(self) -> MCPStatus:
        return self.status

    async def stop(self) -> None:
        return None

    def mne_capability_status(self) -> str:
        return "missing"

    def clear_controller_endpoint(self) -> None:
        return None


class LauncherReadinessTests(unittest.TestCase):
    def test_receipt_requires_one_time_ticket_then_reports_ready_idempotently(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-launcher-readiness-") as raw:
            root = Path(raw)
            token = "launcher-readiness-test-token-00000001"
            config = HostConfig(
                database_path=root / "host.sqlite3",
                knowledge_database_path=root / "knowledge.sqlite3",
                static_dir=root / "missing-web-dist",
                launch_token=token,
            )
            app = create_app(config, mcp=_ReadinessMCP())  # type: ignore[arg-type]

            with TestClient(app, base_url="http://localhost:8767") as client:
                issued = client.post(
                    "/api/launcher/handoff",
                    headers={"Authorization": f"Bearer {token}"},
                )
                self.assertEqual(issued.status_code, 201, issued.text)
                payload = issued.json()
                self.assertIn("handoffTicket", payload)
                self.assertIn("readinessReceipt", payload)
                receipt = payload["readinessReceipt"]

                pending = client.get(
                    "/api/launcher/readiness",
                    params={"receipt": receipt},
                    headers={"Authorization": f"Bearer {token}"},
                )
                self.assertEqual(pending.status_code, 202, pending.text)
                self.assertEqual(pending.json()["status"], "pending")

                session = client.post(
                    "/api/session",
                    headers={"Authorization": f"Bearer {payload['handoffTicket']}"},
                )
                self.assertEqual(session.status_code, 200, session.text)
                session_token = session.json()["sessionToken"]

                replay_ticket = client.post(
                    "/api/session",
                    headers={"Authorization": f"Bearer {payload['handoffTicket']}"},
                )
                self.assertEqual(replay_ticket.status_code, 401, replay_ticket.text)

                still_pending = client.get(
                    "/api/launcher/readiness",
                    params={"receipt": receipt},
                    headers={"Authorization": f"Bearer {token}"},
                )
                self.assertEqual(still_pending.status_code, 202, still_pending.text)

                bootstrap = client.get(
                    "/api/v1/bootstrap",
                    headers={"Authorization": f"Bearer {session_token}"},
                )
                self.assertEqual(bootstrap.status_code, 200, bootstrap.text)

                unauthorized = client.get(
                    "/api/launcher/readiness",
                    params={"receipt": receipt},
                )
                self.assertEqual(unauthorized.status_code, 401, unauthorized.text)

                ready = client.get(
                    "/api/launcher/readiness",
                    params={"receipt": receipt},
                    headers={"Authorization": f"Bearer {token}"},
                )
                self.assertEqual(ready.status_code, 200, ready.text)
                self.assertEqual(ready.json()["status"], "ready")

                repeated_ready = client.get(
                    "/api/launcher/readiness",
                    params={"receipt": receipt},
                    headers={"Authorization": f"Bearer {token}"},
                )
                self.assertEqual(repeated_ready.status_code, 200, repeated_ready.text)
                self.assertEqual(repeated_ready.json()["status"], "ready")


@unittest.skipUnless(
    _MCP_COMMAND.is_file(),
    "the sibling controlstudio-plc-mcp executable is required for the startup contract test",
)
class MCPGatewayContractStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_accepts_live_mcp_product_manifest(self) -> None:
        """Exercise the Host parser against the MCP's actual typed manifest."""

        gateway = MCPGateway(
            command=str(_MCP_COMMAND),
            cwd=str(_MCP_ROOT),
            init_timeout_seconds=30.0,
        )
        try:
            status = await gateway.start()
            self.assertEqual(status.status, "ready")
            self.assertEqual(status.tool_count, 70)
            self.assertEqual(status.agent_tool_count, 26)
        finally:
            await gateway.stop()


if __name__ == "__main__":
    unittest.main()
