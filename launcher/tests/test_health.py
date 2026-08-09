from __future__ import annotations

import hashlib
import hmac
import json
from urllib.error import URLError

import pytest

from controlstudio_launcher.health import (
    HealthState,
    HttpHealthProbe,
    validate_health_payload,
)


@pytest.fixture
def valid_payload() -> dict[str, str]:
    return {
        "service": "controlstudio-host",
        "build_id": "build-1",
        "api_version": "1",
        "status": "ready",
    }


def test_exact_host_identity_and_build_are_required(
    valid_payload: dict[str, str],
) -> None:
    result = validate_health_payload(valid_payload, expected_build="build-1")

    assert result.state is HealthState.READY


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("service", "other-service"),
        ("build_id", "other-build"),
        ("api_version", "2"),
    ],
)
def test_identity_mismatch_is_unexpected(
    valid_payload: dict[str, str],
    field: str,
    value: str,
) -> None:
    valid_payload[field] = value

    result = validate_health_payload(valid_payload, expected_build="build-1")

    assert result.state is HealthState.UNEXPECTED
    assert field in result.detail


def test_starting_is_not_treated_as_ready(valid_payload: dict[str, str]) -> None:
    valid_payload["status"] = "starting"

    result = validate_health_payload(valid_payload, expected_build="build-1")

    assert result.state is HealthState.STARTING


def test_unknown_status_fails_closed(valid_payload: dict[str, str]) -> None:
    valid_payload["status"] = "degraded"

    result = validate_health_payload(valid_payload, expected_build="build-1")

    assert result.state is HealthState.UNEXPECTED


def test_launch_challenge_requires_hmac_from_stored_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nonce = "n" * 43
    launch_token = "launcher-secret-token-that-is-long-enough"
    proof = hmac.new(
        launch_token.encode(),
        b"controlstudio-launcher-v1\0" + nonce.encode(),
        hashlib.sha256,
    ).hexdigest()

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return json.dumps(
                {
                    "algorithm": "HMAC-SHA256",
                    "nonce": nonce,
                    "proof": proof,
                }
            ).encode()

    class Opener:
        def open(self, request, timeout):
            assert f"nonce={nonce}" in request.full_url
            assert timeout == 1
            return Response()

    monkeypatch.setattr(
        "controlstudio_launcher.health.secrets.token_urlsafe",
        lambda _length: nonce,
    )
    probe = HttpHealthProbe(expected_build="build-1", timeout_seconds=1)
    probe._opener = Opener()

    assert probe.verify_launch_token(launch_token).verified is True
    assert probe.verify_launch_token(launch_token + "-wrong").verified is False


def test_launch_challenge_timeout_is_retryable() -> None:
    class Opener:
        def open(self, _request, timeout):
            assert timeout == 1
            raise URLError(TimeoutError("timed out"))

    probe = HttpHealthProbe(expected_build="build-1", timeout_seconds=1)
    probe._opener = Opener()

    result = probe.verify_launch_token("launcher-secret-token-that-is-long-enough")

    assert result.verified is False
    assert result.retryable is True
    assert result.detail == "launch challenge could not be verified: <urlopen error timed out>"


def test_browser_handoff_uses_launcher_credential_only_over_loopback() -> None:
    launch_token = "launcher-secret-token-that-is-long-enough"
    ticket = "one-time-browser-handoff-ticket-that-is-long-enough"

    class Response:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return json.dumps(
                {
                    "handoffTicket": ticket,
                    "expiresAt": "2030-01-01T00:00:00Z",
                }
            ).encode()

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == "http://127.0.0.1:8765/api/launcher/handoff"
            assert request.method == "POST"
            assert request.get_header("Authorization") == f"Bearer {launch_token}"
            assert timeout == 1
            return Response()

    probe = HttpHealthProbe(expected_build="build-1", timeout_seconds=1)
    probe._opener = Opener()

    handoff = probe.issue_browser_handoff(launch_token)

    assert handoff.issued is True
    assert handoff.ticket == ticket
