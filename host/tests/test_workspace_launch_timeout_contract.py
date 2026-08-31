from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import patch

from fastapi.testclient import TestClient

from controlstudio_host import app as host_app
from controlstudio_host.api_models import (
    AttachXG5000RequestV1,
    EngineeringSystemStatusV1,
    LaunchIdleXG5000RequestV1,
    ResumeLocalWorkspaceRequestV1,
    ReviewedNoSaveCloseRequestV1,
)
from controlstudio_host.app import create_app
from controlstudio_host.config import HostConfig
from controlstudio_host.mcp_client import (
    HOST_WORKSPACE_MCP_TOOLS,
    HOST_WORKSPACE_LAUNCH_TRANSPORT_TIMEOUT_SECONDS,
    MCP_WORKSPACE_LAUNCH_OPERATION_TIMEOUT_SECONDS,
    MCPGateway,
    MCPStatus,
    MCPToolError,
    MCPTransportError,
    MUTATING_MCP_TOOLS,
)
from controlstudio_host.store import HostStore


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append((name, dict(arguments), dict(kwargs)))
        return {"ok": True}


class _ReviewedCloseSuccessMCP:
    def __init__(self, *, project_path: str, project_hash: str) -> None:
        self.project_path = project_path
        self.project_hash = project_hash
        self.status = MCPStatus(status="ready")
        self.agent_session: dict[str, Any] | None = None
        self.workspace_calls: list[tuple[str, dict[str, Any]]] = []

    async def start(self) -> MCPStatus:
        return self.status

    async def stop(self) -> None:
        return None

    def mne_capability_status(self) -> str:
        return "missing"

    def bind_agent_session(self, session: dict[str, Any]) -> None:
        self.agent_session = dict(session)

    def bind_agent_workspace(self, _workspace: dict[str, Any]) -> None:
        return None

    def clear_agent_session(self) -> None:
        self.agent_session = None

    def clear_controller_endpoint(self) -> None:
        return None

    @property
    def agent_session_identity_snapshot(self) -> dict[str, Any] | None:
        return dict(self.agent_session) if self.agent_session is not None else None

    async def call_read_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if name != "xg5000.attach":
            raise AssertionError(f"unexpected read tool: {name}")
        if arguments.get("pid") != 101 or arguments.get("hwnd") != 202:
            raise AssertionError(f"unexpected attach identity: {arguments}")
        return {
            "schema_version": "1",
            "session_id": "reviewed-close-session",
            "pid": 101,
            "hwnd": 202,
            "stage": "ANALYSIS_READY",
            "project_path": self.project_path,
            "project_hash": self.project_hash,
            "metadata": {
                "project_identity_verified_against_selected_pid": True,
                "project_identity_read_only_bound_to_selected_pid": True,
                "project_identity_assurance": "strong_readback",
                "project_identity_proof": {
                    "pid": 101,
                    "main_hwnd": 202,
                    "process_create_time": 123.0,
                    "project_path": self.project_path,
                    "project_hash": self.project_hash,
                },
            },
        }

    async def call_workspace_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if name != "xg5000.reviewed_no_save_close":
            raise AssertionError(f"unexpected workspace tool: {name}")
        self.workspace_calls.append((name, dict(arguments)))
        return {
            "schema": "controlstudio-reviewed-no-save-close-cli/v1",
            "operation": "xg5000.reviewed_no_save_close",
            "ok": True,
            "verified": True,
            "source_closed": True,
            "automatic_workspace_open": False,
            "process_terminated": False,
            "reviewed_binding": {
                "session_id": "reviewed-close-session",
                "pid": 101,
                "hwnd": 202,
                "process_create_time": 123.0,
                "project_path": self.project_path,
                "project_hash": self.project_hash,
            },
            "close": {
                "button_id": None,
                "close_action": "wm_close_then_exact_idno_if_prompted",
                "idno_clicked": False,
            },
            "next_step": "explicit_user_confirmed_workspace_open_required",
        }


class _IdleLaunchMCP:
    def __init__(self) -> None:
        self.status = MCPStatus(status="ready")
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.attention_reason: str | None = None

    async def start(self) -> MCPStatus:
        return self.status

    async def stop(self) -> None:
        return None

    def mne_capability_status(self) -> str:
        return "missing"

    def bind_agent_workspace(self, _workspace: dict[str, Any]) -> None:
        return None

    def clear_agent_session(self) -> None:
        return None

    def clear_controller_endpoint(self) -> None:
        return None

    @property
    def agent_session_identity_snapshot(self) -> None:
        return None

    async def call_workspace_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        if name != "xg5000.launch_idle":
            raise AssertionError(f"unexpected workspace tool: {name}")
        binding = {
            "pid": 7024,
            "hwnd": 67344,
            "process_create_time": 123.0,
            "executable": "C:/XG5000/XG5000.exe",
        }
        if self.attention_reason is None:
            return {
                "schema": "controlstudio-idle-xg5000-launch-cli/v1",
                "operation": "xg5000.launch_idle",
                "ok": True,
                "verified": True,
                "status": "launched",
                "launch_action": "process_start",
                "idle_binding": binding,
                "no_existing_instances": True,
                "blank_window": True,
                "modal_observed": False,
                "attention_reason": None,
                "process_exited": False,
                "process_terminated": False,
            }
        return {
            "schema": "controlstudio-idle-xg5000-launch-cli/v1",
            "operation": "xg5000.launch_idle",
            "ok": True,
            "verified": False,
            "status": "needs_attention",
            "launch_action": "process_start",
            "idle_binding": None if self.attention_reason == "launch_timeout" else binding,
            "no_existing_instances": True,
            "blank_window": False,
            "modal_observed": self.attention_reason == "owned_modal",
            "attention_reason": self.attention_reason,
            "process_exited": self.attention_reason == "process_exited",
            "process_terminated": False,
        }


class WorkspaceLaunchTimeoutContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_project_transport_outlives_mcp_cleanup_contract(self) -> None:
        self.assertEqual(ResumeLocalWorkspaceRequestV1(timeout_seconds=120).timeout_seconds, 120)
        with self.assertRaises(ValueError):
            ResumeLocalWorkspaceRequestV1(timeout_seconds=120.1)
        self.assertEqual(MCP_WORKSPACE_LAUNCH_OPERATION_TIMEOUT_SECONDS, 135)
        self.assertEqual(HOST_WORKSPACE_LAUNCH_TRANSPORT_TIMEOUT_SECONDS, 140)
        self.assertGreater(
            HOST_WORKSPACE_LAUNCH_TRANSPORT_TIMEOUT_SECONDS,
            MCP_WORKSPACE_LAUNCH_OPERATION_TIMEOUT_SECONDS,
        )

        client = _RecordingClient()
        gateway = MCPGateway(command="unused")
        gateway._client = cast(Any, client)
        gateway._status = MCPStatus(status="ready")
        arguments = {
            "workspace_id": "11111111-1111-4111-8111-111111111111",
            "expected_working_hash": "a" * 64,
            "timeout_seconds": 120,
        }
        with patch.object(
            MCPGateway,
            "_decode_tool_result",
            return_value={"ok": True},
        ):
            await gateway.call_workspace_tool("xg5000.open_project", arguments)

        self.assertEqual(
            client.calls,
            [
                (
                    "xg5000.open_project",
                    arguments,
                    {"timeout": HOST_WORKSPACE_LAUNCH_TRANSPORT_TIMEOUT_SECONDS},
                )
            ],
        )

    async def test_other_workspace_tools_keep_their_existing_timeout_behavior(self) -> None:
        client = _RecordingClient()
        gateway = MCPGateway(command="unused")
        gateway._client = cast(Any, client)
        gateway._status = MCPStatus(status="ready")
        arguments = {"workspace_id": "11111111-1111-4111-8111-111111111111"}
        with patch.object(
            MCPGateway,
            "_decode_tool_result",
            return_value={"ok": True},
        ):
            await gateway.call_workspace_tool("workspace.status", arguments)

        self.assertEqual(client.calls, [("workspace.status", arguments, {})])

    async def test_reviewed_close_is_host_workspace_only_and_uses_normal_transport(self) -> None:
        client = _RecordingClient()
        gateway = MCPGateway(command="unused")
        gateway._client = cast(Any, client)
        gateway._status = MCPStatus(status="ready")
        arguments = {
            "session_id": "session-1",
            "pid": 101,
            "hwnd": 202,
            "process_create_time": 123.0,
            "project_path": "C:/work/project.xgwx",
            "project_hash": "a" * 64,
            "confirm_no_save": True,
        }
        with patch.object(MCPGateway, "_decode_tool_result", return_value={"ok": True}):
            await gateway.call_workspace_tool("xg5000.reviewed_no_save_close", arguments)
        self.assertEqual(
            client.calls,
            [("xg5000.reviewed_no_save_close", arguments, {})],
        )

    async def test_idle_close_is_host_workspace_only_and_uses_normal_transport(self) -> None:
        self.assertIn("xg5000.close_idle_instance", HOST_WORKSPACE_MCP_TOOLS)
        self.assertIn("xg5000.close_idle_instance", MUTATING_MCP_TOOLS)
        client = _RecordingClient()
        gateway = MCPGateway(command="unused")
        gateway._client = cast(Any, client)
        gateway._status = MCPStatus(status="ready")
        arguments = {
            "pid": 7024,
            "hwnd": 67344,
            "process_create_time": 123.0,
            "confirm_idle_close": True,
            "timeout_seconds": 12,
        }
        with patch.object(MCPGateway, "_decode_tool_result", return_value={"ok": True}):
            await gateway.call_workspace_tool("xg5000.close_idle_instance", arguments)
        self.assertEqual(
            client.calls,
            [("xg5000.close_idle_instance", arguments, {})],
        )

    async def test_idle_launch_is_host_workspace_only_and_uses_normal_transport(self) -> None:
        self.assertIn("xg5000.launch_idle", HOST_WORKSPACE_MCP_TOOLS)
        self.assertIn("xg5000.launch_idle", MUTATING_MCP_TOOLS)
        client = _RecordingClient()
        gateway = MCPGateway(command="unused")
        gateway._client = cast(Any, client)
        gateway._status = MCPStatus(status="ready")
        arguments = {"confirm_idle_launch": True, "timeout_seconds": 20}
        with patch.object(MCPGateway, "_decode_tool_result", return_value={"ok": True}):
            await gateway.call_workspace_tool("xg5000.launch_idle", arguments)
        self.assertEqual(client.calls, [("xg5000.launch_idle", arguments, {})])

    def test_idle_launch_returns_a_fresh_binding_or_safe_attention(self) -> None:
        request = LaunchIdleXG5000RequestV1.model_validate(
            {"confirmIdleLaunch": True}
        )
        self.assertEqual(
            request.model_dump(mode="json", by_alias=True)["confirmIdleLaunch"],
            True,
        )
        with self.assertRaises(ValueError):
            LaunchIdleXG5000RequestV1.model_validate(
                {"confirmIdleLaunch": False}
            )

        with TemporaryDirectory(prefix="controlstudio-idle-launch-") as raw_directory:
            root = Path(raw_directory)
            config = HostConfig(
                database_path=root / "host.sqlite3",
                knowledge_database_path=root / "knowledge.sqlite3",
                static_dir=root / "missing-web-dist",
                launch_token="idle-launch-test-token-0123456789",
                release_channel="production",
            )
            store = HostStore(config.database_path)
            mcp = _IdleLaunchMCP()
            app = create_app(config, store=store, mcp=cast(Any, mcp))

            with TestClient(app, base_url="http://localhost:8767") as client:
                handoff = client.post(
                    "/api/launcher/handoff",
                    headers={"Authorization": f"Bearer {config.launch_token}"},
                )
                self.assertEqual(handoff.status_code, 201, handoff.text)
                browser_session = client.post(
                    "/api/session",
                    headers={"Authorization": f"Bearer {handoff.json()['handoffTicket']}"},
                )
                self.assertEqual(browser_session.status_code, 200, browser_session.text)
                client.headers.update(
                    {"Authorization": f"Bearer {browser_session.json()['sessionToken']}"}
                )
                bootstrap = client.get("/api/v1/bootstrap")
                self.assertEqual(bootstrap.status_code, 200, bootstrap.text)
                headers = {"X-ControlStudio-CSRF": bootstrap.json()["csrfToken"]}
                launched = client.post(
                    "/api/v1/xg5000/launch-idle",
                    headers=headers,
                    json={"confirmIdleLaunch": True},
                )
                mcp.attention_reason = "launch_timeout"
                attention = client.post(
                    "/api/v1/xg5000/launch-idle",
                    headers=headers,
                    json={"confirmIdleLaunch": True},
                )

            self.assertEqual(launched.status_code, 200, launched.text)
            self.assertEqual(launched.json()["status"], "launched")
            self.assertEqual(launched.json()["idleBinding"]["pid"], 7024)
            self.assertEqual(attention.status_code, 409, attention.text)
            self.assertEqual(
                attention.json()["detail"]["attentionReason"],
                "launch_timeout",
            )
            self.assertIsNone(attention.json()["detail"]["idleBinding"])
            self.assertEqual(
                mcp.calls,
                [
                    ("xg5000.launch_idle", {"confirm_idle_launch": True, "timeout_seconds": 20}),
                    ("xg5000.launch_idle", {"confirm_idle_launch": True, "timeout_seconds": 20}),
                ],
            )
            events = store.list_events(limit=100)
            self.assertTrue(any(event.event_type == "xg5000.idle_launched" for event in events))
            self.assertTrue(
                any(
                    event.event_type == "xg5000.idle_launch_needs_attention"
                    for event in events
                )
            )

    def test_transport_timeout_routes_only_to_late_attach_reconciliation(self) -> None:
        self.assertTrue(issubclass(MCPTransportError, MCPToolError))
        source = Path(host_app.__file__).read_text(encoding="utf-8")
        helper = source.index("async def open_workspace_in_xg5000(")
        failure = source.index("except MCPToolError as exc:", helper)
        end = source.index("if not isinstance(raw_launch, dict):", failure)
        failure_branch = source[failure:end]
        self.assertIn("_reconcile_late_workspace_open(", failure_branch)
        self.assertNotIn('call_workspace_tool("xg5000.open_project"', failure_branch)
        self.assertIn("working copy는 보존되어 있습니다.", failure_branch)
        self.assertIn("XG5000 열기를 완료하지 못했습니다.", failure_branch)
        self.assertIn('f"진단: {type(exc).__name__}: {exc}"', failure_branch)
        self.assertNotIn("XG5000 설치와 파일 연결", failure_branch)

    def test_reviewed_close_preserves_only_a_bounded_mcp_failure_reason(self) -> None:
        safe = MCPToolError(
            "xg5000.reviewed_no_save_close was rejected by MCP: "
            "standalone XG5000 CLI 'reviewed-no-save-close' failed: "
            "workspace_cli_failure(kind=DialogError,message=the save dialog was not observed)"
        )
        self.assertEqual(
            host_app._reviewed_no_save_close_failure_detail(safe),
            "저장 없이 닫기 검토를 완료하지 못했습니다: DialogError: "
            "the save dialog was not observed. 대상 창은 계속 선택되어 있습니다.",
        )
        self.assertEqual(
            host_app._reviewed_no_save_close_failure_detail(
                MCPToolError(
                    "xg5000.reviewed_no_save_close was rejected by MCP: "
                    "standalone XG5000 CLI 'reviewed-no-save-close' failed: "
                    "workspace_cli_failure(kind=CLITimeout,message=the authorized CLI "
                    "command exceeded its bounded deadline)"
                )
            ),
            "저장 없이 닫기 검토를 완료하지 못했습니다: CLITimeout: "
            "the authorized CLI command exceeded its bounded deadline. "
            "대상 창은 계속 선택되어 있습니다.",
        )
        self.assertEqual(
            host_app._reviewed_no_save_close_failure_detail(
                MCPToolError("token=must-not-echo")
            ),
            "저장 없이 닫기 검토를 완료하지 못했습니다. 대상 창은 계속 선택되어 있습니다.",
        )

        source = Path(host_app.__file__).read_text(encoding="utf-8")
        handler = source.index("async def reviewed_no_save_close_v1(")
        failure = source.index("except MCPToolError as exc:", handler)
        cleared = source.index("active_engineering_session = None", handler)
        self.assertIn("_reviewed_no_save_close_failure_detail(exc)", source[failure:cleared])
        self.assertGreater(cleared, failure)

    def test_reviewed_close_projects_the_actual_cli_success_receipt(self) -> None:
        # This is the result object inside the adapter's successful CLI envelope.
        raw = {
            "schema": "controlstudio-reviewed-no-save-close-cli/v1",
            "operation": "xg5000.reviewed_no_save_close",
            "ok": True,
            "verified": True,
            "source_closed": True,
            "automatic_workspace_open": False,
            "process_terminated": False,
            "reviewed_binding": {
                "session_id": "session-1",
                "pid": 101,
                "hwnd": 202,
                "process_create_time": 123.0,
                "project_path": "C:/work/project.xgwx",
                "project_hash": "a" * 64,
            },
            "close": {
                "button_id": None,
                "close_action": "wm_close_then_exact_idno_if_prompted",
                "idno_clicked": False,
            },
            "next_step": "explicit_user_confirmed_workspace_open_required",
        }

        receipt_digest = host_app._canonical_json_digest(raw)
        completed = host_app._reviewed_no_save_close_response(
            raw,
            receipt_digest=receipt_digest,
        )

        self.assertEqual(
            completed.model_dump(),
            {
                "operation": "xg5000.reviewed_no_save_close",
                "verified": True,
                "source_closed": True,
                "automatic_workspace_open": False,
                "process_terminated": False,
                "reviewed_binding": raw["reviewed_binding"],
                "close": raw["close"],
                "receipt_digest": receipt_digest,
                "next_step": "explicit_user_confirmed_workspace_open_required",
            },
        )

    def test_reviewed_close_success_projects_cli_receipt_and_clears_selected_session(self) -> None:
        with TemporaryDirectory(prefix="controlstudio-reviewed-close-") as raw_directory:
            root = Path(raw_directory)
            project_path = str((root / "working" / "machine.xgwx").resolve())
            project_hash = "a" * 64
            config = HostConfig(
                database_path=root / "host.sqlite3",
                knowledge_database_path=root / "knowledge.sqlite3",
                static_dir=root / "missing-web-dist",
                launch_token="reviewed-close-success-test-token-0123456789",
                release_channel="production",
            )
            store = HostStore(config.database_path)
            mcp = _ReviewedCloseSuccessMCP(
                project_path=project_path,
                project_hash=project_hash,
            )
            app = create_app(config, store=store, mcp=cast(Any, mcp))

            with TestClient(app, base_url="http://localhost:8767") as client:
                handoff = client.post(
                    "/api/launcher/handoff",
                    headers={"Authorization": f"Bearer {config.launch_token}"},
                )
                self.assertEqual(handoff.status_code, 201, handoff.text)
                browser_session = client.post(
                    "/api/session",
                    headers={"Authorization": f"Bearer {handoff.json()['handoffTicket']}"},
                )
                self.assertEqual(browser_session.status_code, 200, browser_session.text)
                client.headers.update(
                    {"Authorization": f"Bearer {browser_session.json()['sessionToken']}"}
                )
                bootstrap = client.get("/api/v1/bootstrap")
                self.assertEqual(bootstrap.status_code, 200, bootstrap.text)

                attached = client.post(
                    "/api/v1/xg5000/attach",
                    json={
                        "pid": 101,
                        "hwnd": 202,
                        "projectPath": project_path,
                        "projectHash": project_hash,
                    },
                )
                self.assertEqual(attached.status_code, 200, attached.text)

                closed = client.post(
                    "/api/v1/xg5000/reviewed-no-save-close",
                    headers={"X-ControlStudio-CSRF": bootstrap.json()["csrfToken"]},
                    json={
                        "sessionId": "reviewed-close-session",
                        "pid": 101,
                        "hwnd": 202,
                        "processCreateTime": 123.0,
                        "projectPath": project_path,
                        "projectHash": project_hash,
                        "confirmNoSave": True,
                    },
                )
                self.assertEqual(closed.status_code, 200, closed.text)
                self.assertNotIn("schema", closed.json())
                self.assertNotIn("ok", closed.json())
                self.assertEqual(closed.json()["sourceClosed"], True)
                self.assertEqual(mcp.agent_session, None)
                self.assertEqual(
                    client.get("/api/v1/engineering/programs").status_code,
                    409,
                )

            events = store.list_events(limit=100)
            self.assertTrue(
                any(event.event_type == "xg5000.reviewed_no_save_closed" for event in events)
            )

    def test_unbound_existing_target_does_not_inherit_active_workspace_identity(self) -> None:
        request = AttachXG5000RequestV1(
            pid=101,
            hwnd=202,
            allow_unbound_existing_target=True,
        )
        self.assertTrue(request.allow_unbound_existing_target)
        source = Path(host_app.__file__).read_text(encoding="utf-8")
        handler = source.index("async def attach_xg5000_v1(")
        end = source.index("@app.post(\n        \"/api/v1/xg5000/reviewed-no-save-close\"", handler)
        body = source[handler:end]
        self.assertIn("not body.allow_unbound_existing_target", body)
        self.assertIn('arguments["project_path"] = working_copy["path"]', body)
        self.assertIn('arguments["project_hash"] = working_copy["current_sha256"]', body)

    def test_attach_result_requires_exact_read_ready_process_lifetime_binding(self) -> None:
        result = {
            "session_id": "session-1",
            "pid": 101,
            "hwnd": 202,
            "project_path": "C:/work/project.xgwx",
            "project_hash": "a" * 64,
            "metadata": {
                "project_identity_verified_against_selected_pid": False,
                "project_identity_read_only_bound_to_selected_pid": True,
                "project_identity_assurance": "controlled_launch_read_only",
                "controlled_open_read_proof": {"process_create_time": 123.5},
            },
        }
        validated = host_app._validated_engineering_session_result(
            result,
            pid=101,
            hwnd=202,
            project_path="C:/work/project.xgwx",
            project_hash="a" * 64,
        )
        self.assertIs(validated, result)

        missing_lifetime = {
            **result,
            "metadata": {
                **result["metadata"],
                "controlled_open_read_proof": None,
            },
        }
        self.assertIsNone(
            host_app._validated_engineering_session_result(
                missing_lifetime,
                pid=101,
                hwnd=202,
                project_path="C:/work/project.xgwx",
                project_hash="a" * 64,
            )
        )
        self.assertIsNone(
            host_app._validated_engineering_session_result(
                result,
                pid=999,
                hwnd=202,
                project_path="C:/work/project.xgwx",
                project_hash="a" * 64,
            )
        )

    def test_verified_cli_v3_receipt_is_mutation_ready_only_when_exact(self) -> None:
        proof = {
            "method": "controlled_open_verified_cli_receipt_v3",
            "proof_source": "verified_workspace_launch_cli",
            "window_title": "internal-name - XG5000",
            "pid": 101,
            "main_hwnd": 202,
            "workspace_id": "11111111-1111-4111-8111-111111111111",
            "project_path": "C:/work/project.xgwx",
            "project_hash": "a" * 64,
            "process_create_time": 123.5,
        }
        session = {
            "pid": 101,
            "hwnd": 202,
            "project_path": "C:/work/project.xgwx",
            "project_hash": "a" * 64,
            "metadata": {
                "project_identity_verified_against_selected_pid": False,
                "project_identity_read_only_bound_to_selected_pid": True,
                "project_identity_assurance": "controlled_launch_read_only",
                "controlled_open_read_proof": proof,
            },
        }
        self.assertTrue(host_app._engineering_session_identity_verified(session))

        for field, invalid in (
            ("pid", 999),
            ("main_hwnd", 999),
            ("window_title", ""),
            ("process_create_time", 0),
            ("project_hash", "b" * 64),
        ):
            changed = {
                **session,
                "metadata": {
                    **session["metadata"],
                    "controlled_open_read_proof": {**proof, field: invalid},
                },
            }
            self.assertFalse(host_app._engineering_session_identity_verified(changed))

        legacy = {
            **session,
            "metadata": {
                **session["metadata"],
                "controlled_open_read_proof": {
                    **proof,
                    "method": "controlled_open_exact_command_line_workspace_hash_v2",
                },
            },
        }
        self.assertFalse(host_app._engineering_session_identity_verified(legacy))

    def test_native_file_selected_pid_proof_is_read_ready_but_not_mutation_ready(self) -> None:
        session = {
            "pid": 101,
            "hwnd": 202,
            "project_path": "C:/work/project.xgwx",
            "project_hash": "a" * 64,
            "metadata": {
                "project_identity_verified_against_selected_pid": True,
                "project_identity_read_only_bound_to_selected_pid": False,
                "project_identity_assurance": "strong_readback",
                "project_identity_proof": {
                    "method": "selected_pid_exact_window_title_native_file_use_v1",
                    "same_active_object": False,
                    "stable_repeat": True,
                    "pid": 101,
                    "main_hwnd": 202,
                    "project_path": "C:/work/project.xgwx",
                    "project_hash": "a" * 64,
                    "process_create_time": 123.5,
                },
            },
        }

        self.assertTrue(host_app._engineering_session_read_ready(session))
        self.assertFalse(host_app._engineering_session_identity_verified(session))

    def test_native_file_proof_falls_through_to_exact_verified_cli_receipt(self) -> None:
        cli_proof = {
            "method": "controlled_open_verified_cli_receipt_v3",
            "proof_source": "verified_workspace_launch_cli",
            "window_title": "internal-name - XG5000",
            "pid": 101,
            "main_hwnd": 202,
            "workspace_id": "11111111-1111-4111-8111-111111111111",
            "project_path": "C:/work/project.xgwx",
            "project_hash": "a" * 64,
            "process_create_time": 123.5,
        }
        native_proof = {
            "method": "selected_pid_exact_window_title_native_file_use_v1",
            "same_active_object": False,
            "stable_repeat": True,
            "pid": 101,
            "main_hwnd": 202,
            "project_path": "C:/work/project.xgwx",
            "project_hash": "a" * 64,
            "process_create_time": 123.5,
        }
        session = {
            "pid": 101,
            "hwnd": 202,
            "project_path": "C:/work/project.xgwx",
            "project_hash": "a" * 64,
            "metadata": {
                "project_identity_verified_against_selected_pid": True,
                "project_identity_proof": native_proof,
                "project_identity_read_only_bound_to_selected_pid": True,
                "project_identity_assurance": "controlled_launch_read_only",
                "controlled_open_read_proof": cli_proof,
            },
        }

        self.assertTrue(host_app._engineering_session_identity_verified(session))
        invalid = {
            **session,
            "metadata": {
                **session["metadata"],
                "controlled_open_read_proof": {**cli_proof, "process_create_time": 0},
            },
        }
        self.assertFalse(host_app._engineering_session_identity_verified(invalid))

    def test_status_contract_exposes_only_exact_reviewed_close_binding_fields(self) -> None:
        model = EngineeringSystemStatusV1(
            state="ready",
            detail="verified",
            process_id=101,
            window_handle=202,
            process_create_time=123.0,
            session_id="session-1",
            project_path="C:/work/project.xgwx",
            project_hash="a" * 64,
        )
        encoded = model.model_dump(mode="json", by_alias=True)
        self.assertEqual(encoded["processId"], 101)
        self.assertEqual(encoded["windowHandle"], 202)
        self.assertEqual(encoded["processCreateTime"], 123.0)
        self.assertEqual(encoded["sessionId"], "session-1")
        self.assertEqual(encoded["projectPath"], "C:/work/project.xgwx")
        self.assertEqual(encoded["projectHash"], "a" * 64)

    def test_reviewed_close_request_preserves_the_camel_case_browser_contract(self) -> None:
        request = ReviewedNoSaveCloseRequestV1.model_validate(
            {
                "sessionId": "session-1",
                "pid": 101,
                "hwnd": 202,
                "processCreateTime": 123.0,
                "projectPath": "C:/work/project.xgwx",
                "projectHash": "a" * 64,
                "confirmNoSave": True,
            }
        )
        self.assertEqual(
            request.model_dump(mode="json", by_alias=True)["confirmNoSave"],
            True,
        )
        with self.assertRaises(ValueError):
            ReviewedNoSaveCloseRequestV1.model_validate(
                {
                    "sessionId": "session-1",
                    "pid": 101,
                    "hwnd": 202,
                    "processCreateTime": 123.0,
                    "projectPath": "C:/work/project.xgwx",
                    "projectHash": "a" * 64,
                    "confirmNoSave": False,
                }
            )


if __name__ == "__main__":
    unittest.main()
