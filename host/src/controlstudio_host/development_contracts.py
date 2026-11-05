"""Read-only public-contract byte checks for the canonical source-dev workspace.

This module and its child probe use only the standard library. They neither
import an SDK nor install dependencies. Packaged releases retain BOM authority.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path


class DevelopmentContractsError(RuntimeError):
    """A source-dev runtime cannot prove the current public contract bytes."""


def _inventory(root: Path) -> dict[str, str]:
    files = {}
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts:
            continue
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise DevelopmentContractsError("linked public contract path")
        if path.is_file() and path.suffix == ".py":
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if "__init__.py" not in files:
        raise DevelopmentContractsError("public contract package is missing")
    return files


def _source_workspace() -> Path:
    platform = Path(__file__).resolve().parents[3]
    workspace = platform.parent
    if platform.name != "gidong-platform" or not all(
        (workspace / name / ".git").exists()
        for name in ("gidong-platform", "gidong-agent", "gidong-plc-mcp", "controlstudio-xg5000-cli")
    ):
        raise DevelopmentContractsError("source-dev preflight requires the canonical Git workspace")
    return workspace


def _command_parts(value: str) -> tuple[str, ...]:
    try:
        return tuple(part[1:-1] if len(part) >= 2 and part[0] == part[-1] == '"' else part
                     for part in shlex.split(value, posix=False))
    except ValueError:
        return ()


def _matches(parts: tuple[str, ...], executable: Path, arguments: tuple[str, ...] = ()) -> bool:
    try:
        return bool(parts and parts[1:] == arguments and Path(parts[0]).resolve() == executable.resolve())
    except (OSError, ValueError):
        return False


def _selected_interpreters(
    workspace: Path, environment: Mapping[str, str], host_python: Path,
) -> dict[str, Path]:
    def python(repository: str, venv: str = ".venv") -> Path:
        return workspace / repository / venv / "Scripts/python.exe"

    def console(repository: str, name: str, venv: str = ".venv") -> Path:
        return workspace / repository / venv / f"Scripts/{name}.exe"

    expected_host = python("gidong-platform/launcher")
    if host_python.resolve() != expected_host.resolve():
        raise DevelopmentContractsError(f"host_launcher interpreter={host_python}: unsupported source-dev interpreter")
    selected = {"host_launcher": expected_host}
    agent = environment.get("CONTROLSTUDIO_AGENT_COMMAND", "").strip()
    agent_default = console("gidong-agent", "controlstudio-agent")
    agent_parts = _command_parts(agent) if agent else (str(agent_default),)
    for venv in (".venv-dev", ".venv"):
        interpreter = python("gidong-agent", venv)
        if (_matches(agent_parts, interpreter, ("-m", "controlstudio_agent.sidecar"))
                or _matches(agent_parts, console("gidong-agent", "controlstudio-agent", venv))):
            selected["agent"] = interpreter
            break
    else:
        raise DevelopmentContractsError("agent: unsupported source-dev command; use the development launcher")
    mcp = console("gidong-plc-mcp", "controlstudio-plc-mcp")
    if not _matches((environment.get("CONTROLSTUDIO_MCP_COMMAND", str(mcp)),), mcp):
        raise DevelopmentContractsError("mcp: unsupported source-dev command; use the development launcher")
    selected["mcp"] = python("gidong-plc-mcp")
    cli = environment.get("CONTROLSTUDIO_XG5000_CLI_COMMAND", "").strip() or environment.get(
        "CONTROLSTUDIO_XG5000_CLI_EXECUTABLE", ""
    ).strip()
    if not cli:
        # cli_adapter's default is the MCP interpreter, not the CLI venv.
        selected["cli"] = selected["mcp"]
    elif _matches(_command_parts(cli), console("controlstudio-xg5000-cli", "controlstudio-xg5000")):
        selected["cli"] = python("controlstudio-xg5000-cli")
    else:
        raise DevelopmentContractsError("cli: unsupported source-dev command; use the development launcher")
    return selected


def _installed_inventory() -> dict:
    spec = importlib.util.find_spec("controlstudio_contracts")
    if spec is None or spec.origin is None or not spec.submodule_search_locations:
        raise DevelopmentContractsError("public contract package is missing")
    root = Path(spec.origin).resolve().parent
    return {"root": str(root), "version": list(sys.version_info[:3]), "files": _inventory(root)}


def _probe(interpreter: Path, workspace: Path) -> dict:
    if not interpreter.is_file():
        raise DevelopmentContractsError("interpreter is missing")
    try:
        result = subprocess.run(
            [str(interpreter), "-E", "-B", str(Path(__file__).resolve()), "--probe"],
            cwd=workspace, capture_output=True, text=True, encoding="utf-8",
            timeout=15, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode:
            raise DevelopmentContractsError("public contract probe failed")
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict) or not isinstance(payload.get("files"), dict):
            raise DevelopmentContractsError("public contract probe result is invalid")
        return payload
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise DevelopmentContractsError("public contract probe could not complete") from error


def verify_source_development_contracts(
    environment: Mapping[str, str], host_python: Path,
) -> None:
    """Reject drift before source-dev launch/restart, without changing any runtime."""
    if (getattr(sys, "frozen", False)
            or environment.get("CONTROLSTUDIO_RELEASE_CHANNEL") != "development"
            or environment.get("CONTROLSTUDIO_PRODUCT_RELEASE") != "source-dev"):
        return
    try:
        workspace = _source_workspace()
        source = workspace / "gidong-platform/packages/python/controlstudio-contracts/src/controlstudio_contracts"
        expected = _inventory(source)
        for component, interpreter in _selected_interpreters(workspace, environment, host_python).items():
            try:
                payload = _probe(interpreter, workspace)
                if tuple(payload.get("version", ())) < (3, 12):
                    raise DevelopmentContractsError("requires Python 3.12 or newer")
                observed = payload["files"]
                missing = sorted(expected.keys() - observed.keys())
                extra = sorted(observed.keys() - expected.keys())
                changed = sorted(name for name in expected.keys() & observed.keys() if expected[name] != observed[name])
                if missing or extra or changed:
                    raise DevelopmentContractsError(f"missing={missing} extra={extra} changed={changed}")
            except DevelopmentContractsError as error:
                raise DevelopmentContractsError(f"{component} interpreter={interpreter}: {error}") from error
        if _inventory(source) != expected:
            raise DevelopmentContractsError("public contract source changed during preflight")
    except (OSError, TypeError, ValueError) as error:
        raise DevelopmentContractsError("public contract files or runtime selection are unreadable") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    try:
        if arguments.probe:
            print(json.dumps(
                {"version": list(sys.version_info[:3]), "files": {}}
                if sys.version_info < (3, 12) else _installed_inventory()
            ))
        else:
            verify_source_development_contracts(os.environ, Path(sys.executable))
    except DevelopmentContractsError as error:
        print(f"Development public contracts preflight refused: {error}", file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
