from __future__ import annotations

import errno
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .constants import (
    CANONICAL_CHALLENGE_URL,
    CANONICAL_HANDOFF_URL,
    CANONICAL_HEALTH_URL,
    EXPECTED_HOST_API_VERSION,
    EXPECTED_HOST_SERVICE,
)

_MAX_HEALTH_BYTES = 64 * 1024
_LAUNCH_CHALLENGE_PREFIX = b"controlstudio-launcher-v1\0"


class HealthState(Enum):
    ABSENT = "absent"
    STARTING = "starting"
    READY = "ready"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True)
class HealthResult:
    state: HealthState
    detail: str
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class LaunchVerificationResult:
    verified: bool
    detail: str
    retryable: bool = False


@dataclass(frozen=True)
class BrowserHandoffResult:
    issued: bool
    detail: str
    ticket: str | None = None


class HealthProbe(Protocol):
    def probe(self) -> HealthResult: ...

    def verify_launch_token(self, launch_token: str) -> LaunchVerificationResult: ...

    def issue_browser_handoff(self, launch_token: str) -> BrowserHandoffResult: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class HttpHealthProbe:
    """Probe only the fixed, loopback ControlStudio health endpoint."""

    def __init__(self, *, expected_build: str, timeout_seconds: float) -> None:
        self._expected_build = expected_build
        self._timeout_seconds = timeout_seconds
        # A local identity probe must never be routed through a user or
        # corporate HTTP proxy, even if proxy environment variables are set.
        self._opener = build_opener(ProxyHandler({}), _NoRedirect)

    def probe(self) -> HealthResult:
        request = Request(
            CANONICAL_HEALTH_URL,
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                if response.status != 200:
                    return HealthResult(
                        HealthState.UNEXPECTED,
                        f"health endpoint returned HTTP {response.status}",
                    )
                body = response.read(_MAX_HEALTH_BYTES + 1)
        except HTTPError as exc:
            return HealthResult(
                HealthState.UNEXPECTED,
                f"canonical health endpoint returned HTTP {exc.code}",
            )
        except URLError as exc:
            if _is_connection_refused(exc.reason):
                return HealthResult(HealthState.ABSENT, "no service is listening")
            if _is_timeout(exc.reason):
                return HealthResult(
                    HealthState.STARTING,
                    "health endpoint has not answered yet",
                )
            return HealthResult(
                HealthState.UNEXPECTED,
                f"canonical health endpoint could not be verified: {exc.reason}",
            )
        except ConnectionRefusedError:
            return HealthResult(HealthState.ABSENT, "no service is listening")
        except TimeoutError as exc:
            return HealthResult(
                HealthState.STARTING,
                f"health endpoint has not answered yet: {exc}",
            )
        except OSError as exc:
            if _is_connection_refused(exc):
                return HealthResult(HealthState.ABSENT, "no service is listening")
            if _is_timeout(exc):
                return HealthResult(
                    HealthState.STARTING,
                    "health endpoint has not answered yet",
                )
            return HealthResult(
                HealthState.UNEXPECTED,
                f"canonical health endpoint could not be verified: {exc}",
            )

        if len(body) > _MAX_HEALTH_BYTES:
            return HealthResult(HealthState.UNEXPECTED, "health response is too large")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return HealthResult(
                HealthState.UNEXPECTED,
                f"health response is not valid UTF-8 JSON: {exc}",
            )
        return validate_health_payload(payload, expected_build=self._expected_build)

    def verify_launch_token(self, launch_token: str) -> LaunchVerificationResult:
        """Prove the listener knows the launcher-only secret."""

        nonce = secrets.token_urlsafe(32)
        challenge_url = f"{CANONICAL_CHALLENGE_URL}?{urlencode({'nonce': nonce})}"
        request = Request(
            challenge_url,
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                if response.status != 200:
                    return LaunchVerificationResult(
                        False,
                        f"launch challenge returned HTTP {response.status}",
                    )
                body = response.read(_MAX_HEALTH_BYTES + 1)
        except HTTPError as exc:
            return LaunchVerificationResult(
                False,
                f"launch challenge could not be verified: {exc}",
            )
        except URLError as exc:
            return LaunchVerificationResult(
                False,
                f"launch challenge could not be verified: {exc}",
                retryable=(
                    _is_connection_refused(exc.reason) or _is_timeout(exc.reason)
                ),
            )
        except TimeoutError as exc:
            return LaunchVerificationResult(
                False,
                f"launch challenge could not be verified: {exc}",
                retryable=True,
            )
        except OSError as exc:
            return LaunchVerificationResult(
                False,
                f"launch challenge could not be verified: {exc}",
                retryable=_is_connection_refused(exc) or _is_timeout(exc),
            )
        if len(body) > _MAX_HEALTH_BYTES:
            return LaunchVerificationResult(False, "launch challenge response is too large")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return LaunchVerificationResult(
                False,
                f"launch challenge is not valid UTF-8 JSON: {exc}",
            )
        if not isinstance(payload, dict):
            return LaunchVerificationResult(False, "launch challenge must be a JSON object")
        if payload.get("algorithm") != "HMAC-SHA256" or payload.get("nonce") != nonce:
            return LaunchVerificationResult(False, "launch challenge metadata did not match")
        proof = payload.get("proof")
        if not isinstance(proof, str):
            return LaunchVerificationResult(False, "launch challenge proof is missing")
        expected = hmac.new(
            launch_token.encode("utf-8"),
            _LAUNCH_CHALLENGE_PREFIX + nonce.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(proof, expected):
            return LaunchVerificationResult(False, "launch challenge proof did not match")
        return LaunchVerificationResult(True, "verified Host launch challenge")

    def issue_browser_handoff(self, launch_token: str) -> BrowserHandoffResult:
        """Request a short-lived one-time browser ticket over authenticated loopback."""

        request = Request(
            CANONICAL_HANDOFF_URL,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {launch_token}",
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                if response.status != 201:
                    return BrowserHandoffResult(
                        False,
                        f"browser handoff returned HTTP {response.status}",
                    )
                body = response.read(_MAX_HEALTH_BYTES + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            return BrowserHandoffResult(
                False,
                f"browser handoff could not be issued: {exc}",
            )
        if len(body) > _MAX_HEALTH_BYTES:
            return BrowserHandoffResult(False, "browser handoff response is too large")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return BrowserHandoffResult(
                False,
                f"browser handoff is not valid UTF-8 JSON: {exc}",
            )
        if not isinstance(payload, dict):
            return BrowserHandoffResult(False, "browser handoff must be a JSON object")
        ticket = payload.get("handoffTicket")
        if (
            not isinstance(ticket, str)
            or not 32 <= len(ticket) <= 512
            or any(character.isspace() or ord(character) < 33 for character in ticket)
        ):
            return BrowserHandoffResult(False, "browser handoff ticket is invalid")
        return BrowserHandoffResult(True, "issued one-time browser handoff", ticket)


def validate_health_payload(payload: object, *, expected_build: str) -> HealthResult:
    if not isinstance(payload, dict):
        return HealthResult(HealthState.UNEXPECTED, "health response must be a JSON object")

    expected = {
        "service": EXPECTED_HOST_SERVICE,
        "build_id": expected_build,
        "api_version": EXPECTED_HOST_API_VERSION,
    }
    for field, expected_value in expected.items():
        actual = payload.get(field)
        if actual != expected_value:
            return HealthResult(
                HealthState.UNEXPECTED,
                f"unexpected Host {field}: expected {expected_value!r}, received {actual!r}",
                payload,
            )

    status = payload.get("status")
    if status == "ready":
        return HealthResult(HealthState.READY, "expected Host is ready", payload)
    if status in {"starting", "initializing"}:
        return HealthResult(HealthState.STARTING, "expected Host is starting", payload)
    return HealthResult(
        HealthState.UNEXPECTED,
        f"unexpected Host status: {status!r}",
        payload,
    )


def _is_connection_refused(reason: object) -> bool:
    if isinstance(reason, ConnectionRefusedError):
        return True
    return isinstance(reason, OSError) and reason.errno in {
        errno.ECONNREFUSED,
        10061,  # WSAECONNREFUSED
    }


def _is_timeout(reason: object) -> bool:
    if isinstance(reason, TimeoutError):
        return True
    return isinstance(reason, OSError) and reason.errno in {
        errno.ETIMEDOUT,
        10060,  # WSAETIMEDOUT
    }
