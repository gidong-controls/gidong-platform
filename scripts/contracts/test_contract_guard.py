from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _guard_module():
    path = ROOT / "scripts" / "contracts" / "contract_guard.py"
    specification = importlib.util.spec_from_file_location("contract_guard", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_breaking_http_required_field_is_detected_before_snapshot_write() -> None:
    guard = _guard_module()
    previous = {
        "paths": {"/api/v1/example": {"post": {}}},
        "components": {"schemas": {"Example": {"required": ["existing"]}}},
    }
    current = {
        "paths": {"/api/v1/example": {"post": {}}},
        "components": {"schemas": {"Example": {"required": ["existing", "new"]}}},
    }
    assert guard.breaking_changes(previous, current) == ["new required field Example.new"]


def test_committed_contract_snapshots_and_digests_are_current() -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [
            str(ROOT / "packages" / "python" / "controlstudio-contracts" / "src"),
            str(ROOT / "host" / "src"),
            environment.get("PYTHONPATH", ""),
        ]
    )
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "contracts" / "contract_guard.py")],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_web_api_types_are_a_contract_package_reexport() -> None:
    guard = _guard_module()
    assert guard.WEB_TYPES_PATH.read_text(encoding="utf-8") == guard.WEB_TYPES_SINGLE_SOURCE


def test_public_contract_packages_have_no_private_runtime_dependency() -> None:
    forbidden = ("pydantic_ai", "xg5000_control", "fastmcp", "controlstudio_agent")
    package_roots = (
        ROOT / "packages" / "python" / "controlstudio-contracts",
        ROOT / "packages" / "typescript" / "controlstudio-contracts",
    )
    for package_root in package_roots:
        for path in package_root.rglob("*"):
            if path.suffix not in {".py", ".toml", ".ts", ".json"}:
                continue
            text = path.read_text(encoding="utf-8")
            assert not any(item in text for item in forbidden), path
