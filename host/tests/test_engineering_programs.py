from __future__ import annotations

from typing import Any

from conftest import FakeMCP, authenticate_client
from fastapi.testclient import TestClient

from controlstudio_host.app import create_app
from controlstudio_host.config import HostConfig
from controlstudio_host.mcp_client import MCPStatus

PROGRAM_ID = "ld:0123456789abcdef"


class EngineeringProgramMCP(FakeMCP):
    def __init__(self) -> None:
        super().__init__()
        self.read_calls: list[tuple[str, dict[str, Any]]] = []
        self.program_catalog: Any = {
            "programs": [
                {
                    "program_id": PROGRAM_ID,
                    "name": "  초기동작  ",
                    "language": "LD",
                    "order": 3,
                    "editor_hwnd": 333,
                    "mdi_hwnd": 222,
                    "title": "초기동작[Program]",
                    "project_hash": "must-not-leak",
                }
            ],
            "project_hash_before": "must-not-leak",
        }
        self.focus_result: Any = {
            "verified": True,
            "read_only": True,
            "project_unchanged": True,
            "program": {
                "program_id": PROGRAM_ID,
                "name": "초기동작",
                "language": "LD",
                "order": 3,
                "title": "초기동작[Program]",
            },
            "activation": {
                "exact_program_active": True,
                "exact_window_foreground": True,
                "editor_hwnd": 333,
                "mdi_hwnd": 222,
            },
        }

    async def call_read_tool(self, name: str, arguments: dict) -> dict:
        self.read_calls.append((name, dict(arguments)))
        if name == "project.list_programs":
            return self.program_catalog
        if name == "program.focus":
            return self.focus_result
        return await super().call_read_tool(name, arguments)


def _csrf_token(client: TestClient) -> str:
    response = client.get("/api/v1/bootstrap")
    assert response.status_code == 200
    token = response.json()["csrfToken"]
    assert isinstance(token, str)
    assert token
    return token


def _open_read_target(client: TestClient, mcp: EngineeringProgramMCP) -> None:
    response = client.post(
        "/api/v1/workspaces/open-local",
        json={
            "sourcePath": r"C:\projects\machine.xgwx",
            "openInXg5000": True,
            "timeoutSeconds": 10,
        },
    )
    assert response.status_code == 200
    assert mcp.bound_session is not None
    assert response.json()["session"]["metadata"][
        "project_identity_read_only_bound_to_selected_pid"
    ]
    mcp.read_calls.clear()


def test_engineering_program_endpoints_require_authenticated_ready_target(
    host_config: HostConfig,
) -> None:
    mcp = EngineeringProgramMCP()
    with TestClient(
        create_app(host_config, mcp=mcp),
        base_url="http://localhost:8765",
    ) as client:
        authenticate_client(client, host_config)
        csrf = _csrf_token(client)

        unauthenticated_list = client.get(
            "/api/v1/engineering/programs",
            headers={"Authorization": ""},
        )
        unauthenticated_focus = client.post(
            f"/api/v1/engineering/programs/{PROGRAM_ID}/focus",
            headers={
                "Authorization": "",
                "X-ControlStudio-CSRF": csrf,
            },
        )
        no_target_list = client.get("/api/v1/engineering/programs")
        no_target_focus = client.post(
            f"/api/v1/engineering/programs/{PROGRAM_ID}/focus",
            headers={"X-ControlStudio-CSRF": csrf},
        )

    assert unauthenticated_list.status_code == 401
    assert unauthenticated_focus.status_code == 401
    assert no_target_list.status_code == 409
    assert no_target_focus.status_code == 409
    assert mcp.read_calls == []


def test_engineering_program_catalog_is_sanitized_and_focus_is_csrf_protected(
    host_config: HostConfig,
) -> None:
    mcp = EngineeringProgramMCP()
    with TestClient(
        create_app(host_config, mcp=mcp),
        base_url="http://localhost:8765",
    ) as client:
        authenticate_client(client, host_config)
        _open_read_target(client, mcp)

        status_payload = client.get("/api/v1/status").json()
        program_focus_feature = next(
            item
            for item in status_payload["featureReadiness"]
            if item["featureId"] == "program_focus"
        )
        listed = client.get("/api/v1/engineering/programs")
        missing_csrf = client.post(
            f"/api/v1/engineering/programs/{PROGRAM_ID}/focus",
        )
        invalid_csrf = client.post(
            f"/api/v1/engineering/programs/{PROGRAM_ID}/focus",
            headers={"X-ControlStudio-CSRF": "invalid"},
        )
        focused = client.post(
            f"/api/v1/engineering/programs/{PROGRAM_ID}/focus",
            headers={"X-ControlStudio-CSRF": _csrf_token(client)},
        )

    assert listed.status_code == 200
    assert program_focus_feature == {
        "featureId": "program_focus",
        "label": "XG5000 프로그램 위치 열기",
        "maturity": "supported",
        "verification": "real_xg5000",
        "currentReady": True,
        "detail": (
            "정확한 PID/HWND·프로그램·프로젝트 hash 불변 계약으로 실제 "
            "XG5000의 대상 프로그램 전면화를 검증했습니다. 현재 읽기 전용 "
            "엔지니어링 세션이 준비된 경우에만 활성화합니다."
        ),
    }
    assert listed.json() == {
        "programs": [
            {
                "programId": PROGRAM_ID,
                "name": "초기동작",
                "language": "LD",
            }
        ]
    }
    assert "hwnd" not in listed.text.casefold()
    assert "project_hash" not in listed.text.casefold()
    assert "must-not-leak" not in listed.text
    assert missing_csrf.status_code == 403
    assert invalid_csrf.status_code == 403
    assert focused.status_code == 200
    assert focused.json() == {
        "status": "focused",
        "verified": True,
        "readOnly": True,
        "projectUnchanged": True,
        "program": {
            "programId": PROGRAM_ID,
            "name": "초기동작",
            "language": "LD",
        },
    }
    assert mcp.read_calls == [
        ("project.list_programs", {"limit": 500}),
        ("program.focus", {"program_id": PROGRAM_ID}),
    ]


def test_engineering_program_endpoints_fail_closed_on_lost_or_invalid_evidence(
    host_config: HostConfig,
) -> None:
    mcp = EngineeringProgramMCP()
    with TestClient(
        create_app(host_config, mcp=mcp),
        base_url="http://localhost:8765",
    ) as client:
        authenticate_client(client, host_config)
        _open_read_target(client, mcp)
        csrf = _csrf_token(client)

        mcp.status = MCPStatus(status="lost", error="transport lost")
        unavailable = client.get("/api/v1/engineering/programs")
        assert unavailable.status_code == 503
        assert mcp.read_calls == []

        mcp.status = MCPStatus(status="ready")
        mcp.program_catalog = {
            "programs": [
                {
                    "program_id": PROGRAM_ID,
                    "name": "MAIN",
                    "language": "LD",
                },
                {
                    "program_id": PROGRAM_ID,
                    "name": "DUPLICATE",
                    "language": "LD",
                },
            ]
        }
        duplicate = client.get("/api/v1/engineering/programs")
        assert duplicate.status_code == 502

        mcp.focus_result["activation"]["exact_window_foreground"] = False
        unverified_focus = client.post(
            f"/api/v1/engineering/programs/{PROGRAM_ID}/focus",
            headers={"X-ControlStudio-CSRF": csrf},
        )
        invalid_program_id = client.post(
            "/api/v1/engineering/programs/not%20stable/focus",
            headers={"X-ControlStudio-CSRF": csrf},
        )

    assert unverified_focus.status_code == 502
    assert invalid_program_id.status_code == 422
    assert mcp.read_calls == [
        ("project.list_programs", {"limit": 500}),
        ("program.focus", {"program_id": PROGRAM_ID}),
    ]
