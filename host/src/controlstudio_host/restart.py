"""Host-owned, non-interactive replacement bootstrap for development reloads.

The helper is deliberately launched by the already running Host.  It never
accepts a command, executable, working directory, or environment from HTTP;
those values are captured from the running Host process and the replacement is
always ``<current-python> -m controlstudio_host``.  This preserves an elevated
token without asking Windows to elevate a second time.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RESTART_HANDOFF_ENVIRONMENT_VARIABLE = "CONTROLSTUDIO_RESTART_HANDOFF_V1"
_RESTART_MODULE = "controlstudio_host.restart_helper"
_HOST_MODULE = "controlstudio_host"


class RestartBootstrapError(RuntimeError):
    """A replacement could not be prepared without changing the live Host."""


@dataclass(frozen=True)
class RestartHandoff:
    """One authenticated browser session carried only to the replacement Host."""

    receipt: str
    session_token: str
    session_expires_at: datetime

    def encode(self) -> str:
        payload = {
            "receipt": self.receipt,
            "sessionToken": self.session_token,
            "sessionExpiresAt": self.session_expires_at.astimezone(timezone.utc).isoformat(),
        }
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def consume_restart_handoff(environ: Mapping[str, str] | None = None) -> RestartHandoff | None:
    """Read and remove a one-time handoff without ever logging its contents."""

    values: Mapping[str, str] = os.environ if environ is None else environ
    if isinstance(values, dict):
        encoded = values.pop(RESTART_HANDOFF_ENVIRONMENT_VARIABLE, None)
    else:
        encoded = values.get(RESTART_HANDOFF_ENVIRONMENT_VARIABLE)
    if not encoded or len(encoded) > 4096:
        return None
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if not isinstance(payload, dict) or set(payload) != {
            "receipt",
            "sessionToken",
            "sessionExpiresAt",
        }:
            return None
        receipt = payload["receipt"]
        session_token = payload["sessionToken"]
        expires_at = datetime.fromisoformat(payload["sessionExpiresAt"])
        if (
            not isinstance(receipt, str)
            or len(receipt) < 32
            or len(receipt) > 512
            or not isinstance(session_token, str)
            or len(session_token) < 32
            or len(session_token) > 512
            or expires_at.tzinfo is None
            or expires_at <= datetime.now(timezone.utc)
        ):
            return None
        return RestartHandoff(receipt, session_token, expires_at.astimezone(timezone.utc))
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        return None


def _validated_environment(values: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in values.items():
        if (
            isinstance(key, str)
            and isinstance(value, str)
            and key
            and "=" not in key
            and "\x00" not in key
            and "\x00" not in value
        ):
            result[key] = value
    return result


class HostRestartController:
    """Starts only the fixed local replacement helper, never a user command."""

    def __init__(
        self,
        *,
        executable: Path,
        cwd: Path,
        environment: Mapping[str, str],
        port: int,
        parent_pid: int | None = None,
        spawn: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        try:
            resolved_executable = executable.resolve(strict=True)
            resolved_cwd = cwd.resolve(strict=True)
        except OSError as exc:
            raise RestartBootstrapError("the running Host launch context is unavailable") from exc
        if not resolved_executable.is_file() or not resolved_cwd.is_dir():
            raise RestartBootstrapError("the running Host launch context is invalid")
        if not 1 <= port <= 65535:
            raise RestartBootstrapError("the running Host port is invalid")
        self._executable = resolved_executable
        self._cwd = resolved_cwd
        self._environment = _validated_environment(environment)
        self._port = port
        self._parent_pid = os.getpid() if parent_pid is None else parent_pid
        self._spawn = spawn

    @classmethod
    def from_current_process(
        cls,
        *,
        port: int,
        environment: Mapping[str, str],
    ) -> HostRestartController:
        return cls(
            executable=Path(sys.executable),
            cwd=Path.cwd(),
            environment=environment,
            port=port,
        )

    def schedule(self, handoff: RestartHandoff) -> None:
        environment = dict(self._environment)
        environment[RESTART_HANDOFF_ENVIRONMENT_VARIABLE] = handoff.encode()
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        try:
            self._spawn(
                (
                    str(self._executable),
                    "-m",
                    _RESTART_MODULE,
                    "--parent-pid",
                    str(self._parent_pid),
                    "--port",
                    str(self._port),
                ),
                cwd=self._cwd,
                env=environment,
                shell=False,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise RestartBootstrapError("the replacement bootstrap could not be started") from exc
