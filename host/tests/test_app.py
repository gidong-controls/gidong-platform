from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from conftest import FakeMCP, authenticate_client
from fastapi.testclient import TestClient
from starlette.requests import Request

import controlstudio_host.__main__ as host_main
import controlstudio_host.app as host_app
import controlstudio_host.config as host_config_module
from controlstudio_host.app import create_app
from controlstudio_host.config import HostConfig


def test_health_and_bootstrap_match_launcher_contract(client: TestClient) -> None:
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json() == {
        "service": "controlstudio-host",
        "build_id": "0.1.0",
        "api_version": "1",
        "status": "ready",
        "database": "ready",
        "static_ui": "unavailable",
    }

    bootstrap = client.get("/api/bootstrap")
    assert bootstrap.status_code == 200
    payload = bootstrap.json()
    assert payload["service"] == "controlstudio-host"
    assert payload["build_id"] == "0.1.0"
    assert payload["api_version"] == "1"
    assert payload["canonical_origin"] == "http://localhost:8765"
    assert payload["latest_event_sequence"] == 0
    assert payload["features"] == {
        "conversations": True,
        "event_replay": True,
            "mutations": True,
        "service_worker": False,
        "remote_access": False,
    }
    assert payload["host_instance_id"]


def test_conversation_metadata_and_event_replay(client: TestClient) -> None:
    created = client.post("/api/conversations", json={"title": "Packaging line"})
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    appended = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "Why is this sequence slow?"},
    )
    assert appended.status_code == 410
    assert "atomic conversation run endpoint" in appended.json()["detail"]

    detail = client.get(f"/api/conversations/{conversation_id}")
    assert detail.status_code == 200
    assert detail.json()["messages"] == []

    listed = client.get("/api/conversations")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == conversation_id

    replay = client.get("/api/events", params={"follow": "false"})
    assert replay.status_code == 200
    frames = [
        frame for frame in replay.text.strip().split("\n\n") if frame and not frame.startswith(":")
    ]
    assert len(frames) == 1
    first_data = json.loads(
        next(line[6:] for line in frames[0].splitlines() if line.startswith("data: "))
    )
    assert first_data["event_type"] == "conversation.created"

    replay_after_first = client.get(
        "/api/events",
        params={"follow": "false"},
        headers={"Last-Event-ID": str(first_data["sequence"])},
    )
    assert "conversation.created" not in replay_after_first.text
    assert "message.appended" not in replay_after_first.text


def test_missing_conversation_is_not_created_implicitly(client: TestClient) -> None:
    assert client.get("/api/conversations/missing").status_code == 404
    response = client.post(
        "/api/conversations/missing/messages",
        json={"content": "hello"},
    )
    assert response.status_code == 410


def test_cross_origin_requests_are_rejected_without_cors_headers(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/conversations",
        json={"title": "blocked"},
        headers={"Origin": "https://attacker.example"},
    )
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_service_workers_are_always_disabled(client: TestClient) -> None:
    response = client.get("/service-worker.js")
    assert response.status_code == 404
    assert "worker-src 'none'" in response.headers["content-security-policy"]


def test_static_spa_is_served_without_cache_or_service_worker(tmp_path: Path) -> None:
    static_dir = tmp_path / "web" / "dist"
    assets = static_dir / "assets"
    assets.mkdir(parents=True)
    (static_dir / "index.html").write_text("<main>ControlStudio</main>", encoding="utf-8")
    (assets / "app.js").write_text("window.controlStudio = true;", encoding="utf-8")
    (static_dir / "service-worker.js").write_text("self.skipWaiting()", encoding="utf-8")
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=static_dir,
    )

    with TestClient(
        create_app(config, mcp=FakeMCP()),
        base_url="http://localhost:8765",
    ) as static_client:
        authenticate_client(static_client, config)
        root = static_client.get("/")
        assert root.status_code == 200
        assert "ControlStudio" in root.text
        assert root.headers["cache-control"] == "no-store"

        asset = static_client.get("/assets/app.js")
        assert asset.status_code == 200
        assert "controlStudio" in asset.text

        fallback = static_client.get("/conversations/123")
        assert fallback.status_code == 200
        assert "ControlStudio" in fallback.text

        unknown_api = static_client.get("/api/does-not-exist")
        assert unknown_api.status_code == 404
        assert unknown_api.headers["content-type"].startswith("application/json")

        assert static_client.get("/service-worker.js").status_code == 404


def test_non_loopback_configuration_is_refused(tmp_path: Path) -> None:
    try:
        HostConfig(
            bind_host="0.0.0.0",
            database_path=tmp_path / "host.sqlite3",
            static_dir=tmp_path / "dist",
        )
    except ValueError as exc:
        assert "loopback" in str(exc)
    else:
        raise AssertionError("non-loopback bind must fail closed")


def test_module_entrypoint_starts_one_loopback_uvicorn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "dist",
    )
    app_sentinel = object()
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        host_main,
        "join_launcher_job_from_environment",
        lambda: captured.update(job_joined=True),
    )
    monkeypatch.setattr(
        host_config_module.HostConfig,
        "from_env",
        staticmethod(lambda: config),
    )
    app_state = type("AppState", (), {})()
    app_sentinel = type("App", (), {"state": app_state})()
    monkeypatch.setattr(host_app, "create_app", lambda supplied: app_sentinel)

    class FakeConfig:
        def __init__(self, app: object, **kwargs: Any) -> None:
            captured["app"] = app
            captured.update(kwargs)

    class FakeServer:
        def __init__(self, server_config: object) -> None:
            captured["server_config"] = server_config
            self.should_exit = False

        def run(self) -> None:
            captured["ran"] = True
            app_sentinel.state.request_shutdown()
            captured["should_exit"] = self.should_exit

    monkeypatch.setattr(uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    host_main.main()

    assert captured["job_joined"] is True
    assert captured["app"] is app_sentinel
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8765
    assert captured["workers"] == 1
    assert captured["proxy_headers"] is False
    assert captured["server_header"] is False
    assert captured["ran"] is True
    assert captured["should_exit"] is True


def test_launch_session_is_required_for_every_product_api(host_config: HostConfig) -> None:
    app = create_app(host_config, mcp=FakeMCP())
    with TestClient(
        app,
        base_url="http://localhost:8765",
    ) as unauthenticated:
        assert unauthenticated.get("/api/health").status_code == 200
        nonce = "n" * 32
        challenge = unauthenticated.get(
            "/api/launcher/challenge",
            params={"nonce": nonce},
        )
        assert challenge.status_code == 200
        assert (
            challenge.json()["proof"]
            == hmac.new(
                host_config.launch_token.encode(),
                b"controlstudio-launcher-v1\0" + nonce.encode(),
                hashlib.sha256,
            ).hexdigest()
        )
        assert unauthenticated.get("/api/v1/status").status_code == 401
        assert (
            unauthenticated.post(
                "/api/session",
                headers={"Authorization": "Bearer wrong-token"},
            ).status_code
            == 401
        )
        assert (
            unauthenticated.post(
                "/api/session",
                headers={"Authorization": f"Bearer {host_config.launch_token}"},
            ).status_code
            == 401
        )

        first_handoff = unauthenticated.post(
            "/api/launcher/handoff",
            headers={"Authorization": f"Bearer {host_config.launch_token}"},
        )
        second_handoff = unauthenticated.post(
            "/api/launcher/handoff",
            headers={"Authorization": f"Bearer {host_config.launch_token}"},
        )
        assert first_handoff.status_code == 201
        assert second_handoff.status_code == 201
        first_ticket = first_handoff.json()["handoffTicket"]
        second_ticket = second_handoff.json()["handoffTicket"]
        assert first_ticket != second_ticket
        assert host_config.launch_token not in {
            first_ticket,
            second_ticket,
        }

        exchange = unauthenticated.post(
            "/api/session",
            headers={"Authorization": f"Bearer {first_ticket}"},
        )
        assert exchange.status_code == 200
        assert "set-cookie" not in exchange.headers
        session_token = exchange.json()["sessionToken"]
        assert session_token not in {
            host_config.launch_token,
            first_ticket,
            second_ticket,
        }
        assert (
            unauthenticated.post(
                "/api/session",
                headers={"Authorization": f"Bearer {first_ticket}"},
            ).status_code
            == 401
        )
        second_exchange = unauthenticated.post(
            "/api/session",
            headers={"Authorization": f"Bearer {second_ticket}"},
        )
        assert second_exchange.status_code == 200

        expired_handoff = unauthenticated.post(
            "/api/launcher/handoff",
            headers={"Authorization": f"Bearer {host_config.launch_token}"},
        )
        expired_ticket = expired_handoff.json()["handoffTicket"]
        app.state.browser_handoff_tickets[expired_ticket] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        assert (
            unauthenticated.post(
                "/api/session",
                headers={"Authorization": f"Bearer {expired_ticket}"},
            ).status_code
            == 401
        )
        assert expired_ticket not in app.state.browser_handoff_tickets

        refreshed = unauthenticated.post(
            "/api/session/refresh",
            headers={"Authorization": f"Bearer {session_token}"},
        )
        assert refreshed.status_code == 200
        refreshed_token = refreshed.json()["sessionToken"]
        assert refreshed_token != session_token
        assert (
            unauthenticated.get(
                "/api/v1/status",
                headers={"Authorization": f"Bearer {session_token}"},
            ).status_code
            == 401
        )
        app.state.browser_sessions[refreshed_token] = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        )
        grace_refreshed = unauthenticated.post(
            "/api/session/refresh",
            headers={"Authorization": f"Bearer {refreshed_token}"},
        )
        assert grace_refreshed.status_code == 200
        current_token = grace_refreshed.json()["sessionToken"]
        app.state.browser_sessions[current_token] = (
            datetime.now(timezone.utc) - timedelta(hours=25)
        )
        irrecoverable = unauthenticated.post(
            "/api/session/refresh",
            headers={"Authorization": f"Bearer {current_token}"},
        )
        assert irrecoverable.status_code == 401
        assert current_token not in app.state.browser_sessions

        replacement_handoff = unauthenticated.post(
            "/api/launcher/handoff",
            headers={"Authorization": f"Bearer {host_config.launch_token}"},
        )
        replacement = unauthenticated.post(
            "/api/session",
            headers={
                "Authorization": (
                    f"Bearer {replacement_handoff.json()['handoffTicket']}"
                )
            },
        )
        current_token = replacement.json()["sessionToken"]
        unauthenticated.headers.update({"Authorization": f"Bearer {current_token}"})
        assert unauthenticated.get("/api/v1/status").status_code == 200


def test_explicit_authenticated_exit_requests_server_shutdown(
    host_config: HostConfig,
) -> None:
    app = create_app(host_config, mcp=FakeMCP())
    requested: list[bool] = []
    app.state.request_shutdown = lambda: requested.append(True)
    with TestClient(app, base_url="http://localhost:8765") as exit_client:
        authenticate_client(exit_client, host_config)
        conversation = exit_client.post(
            "/api/v1/conversations",
            json={"title": "shutdown race"},
        ).json()
        response = exit_client.post("/api/v1/host/shutdown")
        assert response.status_code == 202
        assert response.json() == {"status": "shutting_down"}
        assert app.state.shutdown_event.is_set()
        rejected_run = exit_client.post(
            f"/api/v1/conversations/{conversation['conversationId']}/messages",
            json={
                "content": "must not start",
                "operationId": "shutdown-race-operation",
            },
            headers={"Idempotency-Key": "shutdown-race-operation"},
        )
        assert rejected_run.status_code == 503
        assert rejected_run.json()["detail"] == "ControlStudio Host is shutting down"
        deadline = time.monotonic() + 1
        while not requested and time.monotonic() < deadline:
            time.sleep(0.01)
    assert requested == [True]


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/api/events", "/api/v1/events"])
async def test_shutdown_event_closes_legacy_and_v1_event_streams(
    host_config: HostConfig,
    path: str,
) -> None:
    app = create_app(
        replace(host_config, event_heartbeat_seconds=60),
        mcp=FakeMCP(),
    )
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == path)

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request({"type": "http", "headers": []}, receive)
    response = await endpoint(
        request,
        after_sequence=0,
        conversation_id=None,
        follow=True,
    )
    next_frame = asyncio.create_task(anext(response.body_iterator))
    await asyncio.sleep(0)

    app.state.shutdown_event.set()

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(next_frame, timeout=0.5)


def test_active_run_rejects_shutdown_without_closing_streams(
    host_config: HostConfig,
) -> None:
    app = create_app(host_config, mcp=FakeMCP())
    requested: list[bool] = []
    app.state.request_shutdown = lambda: requested.append(True)

    class ActiveRun:
        @staticmethod
        def done() -> bool:
            return False

    with TestClient(app, base_url="http://localhost:8765") as exit_client:
        authenticate_client(exit_client, host_config)
        app.state.run_tasks["active-run"] = ActiveRun()
        try:
            response = exit_client.post("/api/v1/host/shutdown")
        finally:
            app.state.run_tasks.pop("active-run", None)

    assert response.status_code == 409
    assert app.state.shutdown_event.is_set() is False
    assert requested == []
