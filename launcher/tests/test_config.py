from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from controlstudio_launcher.cli import build_parser, resolve_settings
from controlstudio_launcher.config import (
    apply_config,
    default_config_path,
    default_settings,
    load_config_file,
)
from controlstudio_launcher.errors import ConfigurationError


def test_default_command_targets_host_module() -> None:
    settings = default_settings(python_executable="python-test")

    assert settings.host_command == ("python-test", "-m", "controlstudio_host")


def test_config_command_is_an_argv_array(tmp_path: Path) -> None:
    config_path = tmp_path / "launcher.json"
    config_path.write_text(
        json.dumps(
            {
                "host_command": ["host.exe", "--bind", "127.0.0.1"],
                "host_cwd": str(tmp_path),
                "expected_host_build": "test-build",
            }
        ),
        encoding="utf-8",
    )

    settings = apply_config(default_settings(), load_config_file(config_path))

    assert settings.host_command == ("host.exe", "--bind", "127.0.0.1")
    assert settings.host_cwd == tmp_path
    assert settings.expected_host_build == "test-build"


def test_relative_host_cwd_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="absolute"):
        apply_config(default_settings(), {"host_cwd": "relative/path"})


def test_unknown_config_key_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "launcher.json"
    config_path.write_text('{"remote_command": "bad"}', encoding="utf-8")

    with pytest.raises(ConfigurationError, match="unknown"):
        load_config_file(config_path)


def test_host_args_require_local_executable_override() -> None:
    parser = build_parser()
    arguments = parser.parse_args(["--no-config", "--host-arg=--unsafe"])

    with pytest.raises(ConfigurationError, match="requires"):
        resolve_settings(arguments, python_executable="python-test")


def test_cli_builds_host_argv_without_shell_string() -> None:
    parser = build_parser()
    arguments = parser.parse_args(
        [
            "--no-config",
            "--host-executable",
            "C:/Program Files/ControlStudio/host.exe",
            "--host-arg=--foreground",
            "--expected-host-build",
            "build-123",
        ]
    )

    settings = resolve_settings(arguments, python_executable="python-test")

    assert settings.host_command == (
        "C:/Program Files/ControlStudio/host.exe",
        "--foreground",
    )
    assert settings.expected_host_build == "build-123"


def test_default_config_path_uses_local_app_data() -> None:
    path = default_config_path({"LOCALAPPDATA": "C:/AppData/Local"})

    assert path.as_posix().endswith("ControlStudio/launcher.json")


def test_explicit_missing_config_is_rejected(tmp_path: Path) -> None:
    arguments = argparse.Namespace(
        config=tmp_path / "missing.json",
        no_config=False,
        host_executable=None,
        host_arg=[],
        host_cwd=None,
        expected_host_build=None,
        startup_timeout=None,
    )

    with pytest.raises(ConfigurationError, match="does not exist"):
        resolve_settings(arguments, python_executable="python-test")
