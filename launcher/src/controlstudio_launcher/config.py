from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .constants import DEFAULT_EXPECTED_HOST_BUILD, PRODUCT_DATA_DIRECTORY_NAME
from .errors import ConfigurationError

_ALLOWED_CONFIG_KEYS = {
    "data_directory",
    "host_command",
    "host_cwd",
    "expected_host_build",
    "startup_timeout_seconds",
    "probe_timeout_seconds",
    "poll_interval_seconds",
}


@dataclass(frozen=True)
class LauncherSettings:
    """Locally resolved launcher settings.

    These values are never accepted from the browser or Host API. The default
    command deliberately targets the product Host package without a shell.
    """

    host_command: tuple[str, ...]
    host_cwd: Path | None
    expected_host_build: str
    # Cold Python/Pydantic/MCP startup on Windows with real-time protection can
    # exceed one minute. The launcher still verifies the exact Host identity
    # throughout this bounded wait.
    startup_timeout_seconds: float = 180.0
    probe_timeout_seconds: float = 1.0
    poll_interval_seconds: float = 0.2
    data_directory: Path | None = None


def default_settings(*, python_executable: str | None = None) -> LauncherSettings:
    executable = python_executable or sys.executable
    return LauncherSettings(
        host_command=(executable, "-m", "controlstudio_host"),
        host_cwd=None,
        expected_host_build=DEFAULT_EXPECTED_HOST_BUILD,
    )


def default_config_path(environ: dict[str, str] | None = None) -> Path:
    values = os.environ if environ is None else environ
    local_app_data = values.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / PRODUCT_DATA_DIRECTORY_NAME / "launcher.json"
    return (
        Path.home()
        / ".config"
        / PRODUCT_DATA_DIRECTORY_NAME.casefold().replace(" ", "-")
        / "launcher.json"
    )


def load_config_file(path: Path, *, required: bool = False) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if required:
            raise ConfigurationError(f"launcher config does not exist: {path}") from None
        return {}
    except OSError as exc:
        raise ConfigurationError(f"cannot read launcher config {path}: {exc}") from exc

    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"launcher config is not valid JSON: {exc}") from exc

    if not isinstance(value, dict):
        raise ConfigurationError("launcher config root must be a JSON object")
    unknown = sorted(set(value) - _ALLOWED_CONFIG_KEYS)
    if unknown:
        raise ConfigurationError(f"unknown launcher config keys: {', '.join(unknown)}")
    return value


def apply_config(
    settings: LauncherSettings,
    values: dict[str, Any],
) -> LauncherSettings:
    updates: dict[str, object] = {}

    if "data_directory" in values:
        directory = values["data_directory"]
        if directory is None:
            updates["data_directory"] = None
        elif isinstance(directory, str) and directory:
            path = Path(directory)
            if not path.is_absolute():
                raise ConfigurationError("data_directory must be an absolute path")
            updates["data_directory"] = path
        else:
            raise ConfigurationError("data_directory must be null or a non-empty absolute path")

    if "host_command" in values:
        command = values["host_command"]
        if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
            raise ConfigurationError("host_command must be a JSON array of strings")
        updates["host_command"] = _validate_command(tuple(command))

    if "host_cwd" in values:
        cwd = values["host_cwd"]
        if cwd is None:
            updates["host_cwd"] = None
        elif isinstance(cwd, str) and cwd:
            path = Path(cwd)
            if not path.is_absolute():
                raise ConfigurationError("host_cwd must be an absolute path")
            updates["host_cwd"] = path
        else:
            raise ConfigurationError("host_cwd must be null or a non-empty absolute path")

    if "expected_host_build" in values:
        updates["expected_host_build"] = _validate_text(
            values["expected_host_build"], "expected_host_build"
        )

    for key in (
        "startup_timeout_seconds",
        "probe_timeout_seconds",
        "poll_interval_seconds",
    ):
        if key in values:
            updates[key] = _validate_positive_number(values[key], key)

    return replace(settings, **updates)


def apply_cli_overrides(
    settings: LauncherSettings,
    *,
    host_executable: str | None,
    host_args: list[str] | None,
    host_cwd: str | None,
    expected_host_build: str | None,
    startup_timeout_seconds: float | None,
) -> LauncherSettings:
    updates: dict[str, object] = {}

    if host_args and not host_executable:
        raise ConfigurationError("--host-arg requires --host-executable")
    if host_executable:
        updates["host_command"] = _validate_command((host_executable, *(host_args or ())))
    if host_cwd:
        cwd = Path(host_cwd)
        if not cwd.is_absolute():
            raise ConfigurationError("--host-cwd must be an absolute path")
        updates["host_cwd"] = cwd
    if expected_host_build is not None:
        updates["expected_host_build"] = _validate_text(expected_host_build, "expected_host_build")
    if startup_timeout_seconds is not None:
        updates["startup_timeout_seconds"] = _validate_positive_number(
            startup_timeout_seconds, "startup_timeout_seconds"
        )

    return replace(settings, **updates)


def _validate_command(command: tuple[str, ...]) -> tuple[str, ...]:
    if not command or not command[0].strip():
        raise ConfigurationError("host command must contain a non-empty executable")
    if len(command) > 128:
        raise ConfigurationError("host command contains too many arguments")
    for argument in command:
        if not argument or "\x00" in argument or "\r" in argument or "\n" in argument:
            raise ConfigurationError("host command contains an invalid argument")
    return command


def _validate_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ConfigurationError(f"{field} must be a non-empty string up to 128 characters")
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise ConfigurationError(f"{field} contains an invalid character")
    return value


def _validate_positive_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigurationError(f"{field} must be a number")
    result = float(value)
    if result <= 0 or result > 300:
        raise ConfigurationError(f"{field} must be greater than 0 and at most 300")
    return result
