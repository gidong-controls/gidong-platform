from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from controlstudio_host import restart_helper
from controlstudio_host.app import create_app
from controlstudio_host.config import HostConfig
from controlstudio_host.launch_job import LAUNCH_JOB_ENVIRONMENT_VARIABLE
from controlstudio_host.mcp_client import MCPStatus
from controlstudio_host.restart import (
    RESTART_HANDOFF_ENVIRONMENT_VARIABLE,
    HostRestartController,
    RestartHandoff,
    consume_restart_handoff,
)


class _MCP:
    def __init__(self) -> None:
        self.status = MCPStatus(
            status="ready",
            server_name="restart-test",
            server_version="1.0.0",
            protocol_version="2025-11-25",
            tool_count=0,
            agent_tool_count=0,
        )
        self.controller_observation_current = False

    @property
    def agent_session_identity_snapshot(self):
        return None

    async def start(self):
        return self.status

    async def stop(self) -> None:
        return None

    def mne_capability_status(self) -> str:
        return "missing"

    def clear_controller_endpoint(self) -> None:
        return None


def _authenticated_client(client: TestClient, token: str) -> tuple[dict[str, str], str]:
    issued = client.post("/api/launcher/handoff", headers={"Authorization": f"Bearer {token}"})
    assert issued.status_code == 201, issued.text
    session = client.post(
        "/api/session",
        headers={"Authorization": f"Bearer {issued.json()['handoffTicket']}"},
    )
    assert session.status_code == 200, session.text
    session_token = session.json()["sessionToken"]
    bootstrap = client.get("/api/v1/bootstrap", headers={"Authorization": f"Bearer {session_token}"})
    assert bootstrap.status_code == 200, bootstrap.text
    return (
        {
            "Authorization": f"Bearer {session_token}",
            "X-ControlStudio-CSRF": bootstrap.json()["csrfToken"],
        },
        session_token,
    )


class HostRestartApiTests(unittest.TestCase):
    def _app(self, root: Path):
        token = "restart-api-test-token-000000000001"
        app = create_app(
            HostConfig(
                database_path=root / "host.sqlite3",
                knowledge_database_path=root / "knowledge.sqlite3",
                static_dir=root / "missing-web-dist",
                launch_token=token,
            ),
            mcp=_MCP(),  # type: ignore[arg-type]
        )
        return app, token

    def test_restart_requires_authenticated_csrf_session_and_returns_no_secret(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-host-restart-") as raw:
            app, token = self._app(Path(raw))
            scheduled: list[RestartHandoff] = []
            app.state.request_restart = scheduled.append
            app.state.request_shutdown = lambda: None
            with TestClient(app, base_url="http://localhost:8767") as client:
                denied = client.post("/api/v1/host/restart")
                self.assertEqual(denied.status_code, 401, denied.text)
                headers, session_token = _authenticated_client(client, token)
                no_csrf = client.post(
                    "/api/v1/host/restart",
                    headers={"Authorization": headers["Authorization"]},
                )
                self.assertEqual(no_csrf.status_code, 403, no_csrf.text)
                response = client.post("/api/v1/host/restart", headers=headers)
                self.assertEqual(response.status_code, 202, response.text)
                payload = response.json()
                self.assertEqual(payload["status"], "scheduled")
                self.assertIn("restartReceipt", payload)
                self.assertNotIn("sessionToken", payload)
                self.assertEqual(len(scheduled), 1)
                self.assertEqual(scheduled[0].session_token, session_token)
                status = client.get(
                    f"/api/v1/host/restart/{payload['restartReceipt']}",
                    headers={"Authorization": headers["Authorization"]},
                )
                self.assertEqual(status.status_code, 200, status.text)
                self.assertEqual(status.json()["status"], "scheduled")

    def test_restart_fails_closed_while_a_run_is_active(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-host-restart-") as raw:
            app, token = self._app(Path(raw))
            app.state.request_restart = lambda _handoff: self.fail("restart must not schedule")
            app.state.request_shutdown = lambda: None

            class _ActiveTask:
                def done(self) -> bool:
                    return False

            app.state.run_tasks["active-run"] = _ActiveTask()
            with TestClient(app, base_url="http://localhost:8767") as client:
                headers, _ = _authenticated_client(client, token)
                response = client.post("/api/v1/host/restart", headers=headers)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn("agent run", response.json()["detail"])
                app.state.run_tasks.clear()

    def test_replacement_restores_only_handed_off_session_and_marks_receipt_ready(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-host-restart-") as raw:
            handoff = RestartHandoff(
                receipt="r" * 32,
                session_token="s" * 32,
                session_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
            with patch.dict(
                os.environ,
                {RESTART_HANDOFF_ENVIRONMENT_VARIABLE: handoff.encode()},
            ):
                app, _ = self._app(Path(raw))
            with TestClient(app, base_url="http://localhost:8767") as client:
                headers = {"Authorization": f"Bearer {handoff.session_token}"}
                bootstrap = client.get("/api/v1/bootstrap", headers=headers)
                self.assertEqual(bootstrap.status_code, 200, bootstrap.text)
                ready = client.get(
                    f"/api/v1/host/restart/{handoff.receipt}",
                    headers=headers,
                )
                self.assertEqual(ready.status_code, 200, ready.text)
                self.assertEqual(ready.json()["status"], "ready")

    def test_restart_fails_closed_while_a_target_operation_lease_is_active(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-host-restart-") as raw:
            app, token = self._app(Path(raw))
            app.state.request_restart = lambda _handoff: self.fail("restart must not schedule")
            app.state.request_shutdown = lambda: None
            app.state.store.acquire_target_operation_lease(
                operation_id="restart-lease-test",
                owner_kind="engineering_attach",
                holder_token="restart-lease-holder",
                target_identity={"test": "restart"},
            )
            with TestClient(app, base_url="http://localhost:8767") as client:
                headers, _ = _authenticated_client(client, token)
                response = client.post("/api/v1/host/restart", headers=headers)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn("target operation", response.json()["detail"])
            app.state.store.release_target_operation_lease(
                operation_id="restart-lease-test",
                holder_token="restart-lease-holder",
                reason="restart_test_cleanup",
            )


class RestartBootstrapTests(unittest.TestCase):
    def test_controller_uses_fixed_module_and_preserves_configured_children(self) -> None:
        spawned: list[tuple[tuple[str, ...], dict]] = []

        def spawn(command, **kwargs):
            spawned.append((tuple(command), kwargs))

        executable = Path(__file__).resolve()
        controller = HostRestartController(
            executable=executable,
            cwd=Path.cwd(),
            environment={
                "CONTROLSTUDIO_AGENT_COMMAND": "agent --stdio",
                "CONTROLSTUDIO_MCP_COMMAND": "mcp --stdio",
                "CONTROLSTUDIO_XG5000_CLI_COMMAND": "xg-cli.exe",
                "CONTROLSTUDIO_DEVELOPMENT_MODEL_API_KEY": "not-in-response",
            },
            port=8767,
            parent_pid=1234,
            spawn=spawn,
        )
        handoff = RestartHandoff(
            receipt="r" * 32,
            session_token="s" * 32,
            session_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        controller.schedule(handoff)
        command, kwargs = spawned[0]
        self.assertEqual(command[1:3], ("-m", "controlstudio_host.restart_helper"))
        self.assertNotIn("xg5000", " ".join(command).casefold())
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["env"]["CONTROLSTUDIO_AGENT_COMMAND"], "agent --stdio")
        self.assertEqual(kwargs["env"]["CONTROLSTUDIO_MCP_COMMAND"], "mcp --stdio")
        self.assertEqual(kwargs["env"]["CONTROLSTUDIO_XG5000_CLI_COMMAND"], "xg-cli.exe")
        self.assertIn(RESTART_HANDOFF_ENVIRONMENT_VARIABLE, kwargs["env"])

    def test_restart_handoff_is_one_time_and_restores_only_a_valid_session(self) -> None:
        handoff = RestartHandoff(
            receipt="r" * 32,
            session_token="s" * 32,
            session_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        environment = {RESTART_HANDOFF_ENVIRONMENT_VARIABLE: handoff.encode()}
        restored = consume_restart_handoff(environment)
        self.assertEqual(restored, handoff)
        self.assertNotIn(RESTART_HANDOFF_ENVIRONMENT_VARIABLE, environment)
        self.assertIsNone(consume_restart_handoff(environment))

    def test_helper_becomes_replacement_host_without_a_second_process_hop(self) -> None:
        with (
            patch.object(restart_helper, "_wait_for_parent_exit", return_value=True),
            patch.object(restart_helper, "_wait_for_port_release", return_value=True),
            patch.object(restart_helper, "_run_replacement_host") as run_host,
        ):
            result = restart_helper.restart_after_parent_exits(1234, 8767)

        self.assertEqual(result, 0)
        run_host.assert_called_once_with()

    def test_helper_replacement_does_not_rejoin_inherited_launcher_job(self) -> None:
        observed_job_names: list[str | None] = []

        def run_host() -> None:
            observed_job_names.append(os.environ.get(LAUNCH_JOB_ENVIRONMENT_VARIABLE))

        with (
            patch.dict(
                os.environ,
                {LAUNCH_JOB_ENVIRONMENT_VARIABLE: "ControlStudio-Original-Launch-Job"},
            ),
            patch("controlstudio_host.__main__.main", side_effect=run_host),
        ):
            restart_helper._run_replacement_host()

        self.assertEqual(observed_job_names, [None])

    def test_helper_does_not_start_replacement_before_parent_exits(self) -> None:
        with (
            patch.object(restart_helper, "_wait_for_parent_exit", return_value=False),
            patch.object(restart_helper, "_wait_for_port_release") as wait_for_port,
            patch.object(restart_helper, "_run_replacement_host") as run_host,
        ):
            result = restart_helper.restart_after_parent_exits(1234, 8767)

        self.assertEqual(result, 1)
        wait_for_port.assert_not_called()
        run_host.assert_not_called()


if __name__ == "__main__":
    unittest.main()
