"""PyInstaller entry point for the public Platform launcher.

The release composer supplies private sidecar locations through environment
variables; no private source tree is imported by this entry point.
"""

import hashlib
import json
import os
import sys
from pathlib import Path

_HOST_CHILD_ARGUMENT = "--controlstudio-internal-host"
_CONTRACT_NAMES = {
    "api_contract",
    "event_contract",
    "agent_contract",
    "mcp_product_contract",
    "workspace_contract",
    "audit_contract",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _configure_installed_product() -> None:
    """Bind verified installed sidecars without exposing them as user apps."""

    if not getattr(sys, "frozen", False):
        return
    executable = Path(sys.executable).resolve()
    runtime = executable.parent / "runtime"
    if not runtime.exists():
        return
    bom_path = runtime / "controlstudio.bom.json"
    if not bom_path.is_file():
        raise RuntimeError("installed ControlStudio runtime BOM is missing")
    try:
        bom = json.loads(bom_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("installed ControlStudio runtime BOM is invalid") from exc
    if bom.get("bom_schema") != "controlstudio-bom/v1":
        raise RuntimeError("installed ControlStudio runtime BOM schema is incompatible")
    release_channel = bom.get("release_channel")
    if release_channel not in {"development", "beta", "production"}:
        raise RuntimeError("installed ControlStudio release channel is invalid")
    product_release = bom.get("product_release")
    if not isinstance(product_release, str) or not product_release.strip():
        raise RuntimeError("installed ControlStudio product release is invalid")
    contracts = bom.get("contracts")
    if not isinstance(contracts, dict) or set(contracts) != _CONTRACT_NAMES:
        raise RuntimeError("installed ControlStudio contract set is incomplete")
    components = bom.get("components")
    if not isinstance(components, list):
        raise TypeError("installed ControlStudio component set is invalid")
    by_id = {
        item.get("id"): item
        for item in components
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if set(by_id) != {"platform", "agent", "plc-mcp"}:
        raise RuntimeError("installed ControlStudio component set is incomplete")
    paths = {
        "platform": executable,
        "agent": runtime / "controlstudio-agent.exe",
        "plc-mcp": runtime / "controlstudio-plc-mcp.exe",
    }
    for component_id, path in paths.items():
        record = by_id[component_id]
        expected = record.get("sha256")
        if (
            not path.is_file()
            or not isinstance(expected, str)
            or _sha256(path) != expected
        ):
            raise RuntimeError(
                f"installed ControlStudio {component_id} artifact failed integrity validation"
            )
        if record.get("contracts") != contracts:
            raise RuntimeError(
                f"installed ControlStudio {component_id} contract set mismatched"
            )
    contract_artifact = bom.get("contract_artifact")
    if not isinstance(contract_artifact, dict):
        raise TypeError("installed public contracts artifact is missing")
    contract_name = contract_artifact.get("artifact")
    if not isinstance(contract_name, str) or Path(contract_name).name != contract_name:
        raise RuntimeError("installed public contracts artifact path is invalid")
    contract_path = runtime / contract_name
    if not contract_path.is_file() or _sha256(contract_path) != contract_artifact.get(
        "sha256"
    ):
        raise RuntimeError(
            "installed public contracts artifact failed integrity validation"
        )
    os.environ["CONTROLSTUDIO_AGENT_COMMAND"] = f'"{paths["agent"]}"'
    os.environ["CONTROLSTUDIO_MCP_COMMAND"] = str(paths["plc-mcp"])
    os.environ["CONTROLSTUDIO_PRODUCT_BOM"] = str(bom_path)
    os.environ["CONTROLSTUDIO_RELEASE_CHANNEL"] = release_channel
    os.environ["CONTROLSTUDIO_PRODUCT_RELEASE"] = product_release


if getattr(sys, "frozen", False):
    bundled_web = Path(sys._MEIPASS) / "web-dist"  # type: ignore[attr-defined]
    # A one-file executable is extracted to a fresh temporary directory.  The
    # path is only used for the immutable web asset payload embedded in this
    # executable, never for a checkout-relative web source directory.
    os.environ["CONTROLSTUDIO_WEB_DIST"] = str(bundled_web)

_configure_installed_product()

if _HOST_CHILD_ARGUMENT in sys.argv[1:]:
    # The public Platform is a single executable. The launcher starts the same
    # immutable artifact in this private internal mode instead of looking for
    # a Python/module checkout or a second Host executable.
    sys.argv = [
        sys.argv[0],
        *(item for item in sys.argv[1:] if item != _HOST_CHILD_ARGUMENT),
    ]
    from controlstudio_host.__main__ import main as host_main

    host_main()
    raise SystemExit(0)

from controlstudio_launcher.cli import main as launcher_main

launcher_arguments = list(sys.argv[1:])
if "--host-executable" not in launcher_arguments:
    if getattr(sys, "frozen", False):
        launcher_arguments[:0] = [
            "--host-executable",
            sys.executable,
            f"--host-arg={_HOST_CHILD_ARGUMENT}",
        ]
    else:
        launcher_arguments[:0] = [
            "--host-executable",
            sys.executable,
            "--host-arg=-m",
            "--host-arg=controlstudio_host",
        ]

raise SystemExit(launcher_main(launcher_arguments))
