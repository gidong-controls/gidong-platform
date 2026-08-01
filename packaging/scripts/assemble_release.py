"""Create and verify a portable release from explicit immutable artifacts."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from controlstudio_packaging.bom import PackagingError, assemble, build_bom, verify_bom


def _copy_input(path: Path, destination: Path) -> Path:
    source = path.resolve()
    if not source.is_file():
        raise PackagingError(f"release artifact does not exist: {source}")
    target = destination / source.name
    if target.exists():
        raise PackagingError(f"duplicate release artifact name: {source.name}")
    shutil.copy2(source, target)
    return target


def _wheel_version(path: Path) -> str:
    if path.suffix != ".whl" or not path.name.startswith("controlstudio_contracts-"):
        raise PackagingError("contracts artifact must be a controlstudio-contracts wheel")
    try:
        with zipfile.ZipFile(path) as wheel:
            metadata = next(name for name in wheel.namelist() if name.endswith(".dist-info/METADATA"))
            for line in wheel.read(metadata).decode("utf-8").splitlines():
                if line.startswith("Version: "):
                    return line.removeprefix("Version: ").strip()
    except (OSError, StopIteration, zipfile.BadZipFile) as exc:
        raise PackagingError("contracts wheel metadata cannot be read") from exc
    raise PackagingError("contracts wheel Version metadata is missing")


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble a verified portable release from explicit artifact inputs.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--platform-artifact", type=Path, required=True)
    parser.add_argument("--agent-artifact", type=Path, required=True)
    parser.add_argument("--mcp-artifact", type=Path, required=True)
    parser.add_argument("--contracts-wheel", type=Path, required=True)
    parser.add_argument("--product-release", required=True)
    parser.add_argument("--platform-version", required=True)
    parser.add_argument("--agent-version", required=True)
    parser.add_argument("--mcp-version", required=True)
    parser.add_argument("--contracts-version", required=True)
    parser.add_argument("--contracts", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        print(f"Release assembly refused: output already exists: {output}", file=sys.stderr)
        return 2
    try:
        artifacts = output / "artifacts"
        artifacts.mkdir(parents=True)
        platform = _copy_input(args.platform_artifact, artifacts)
        agent = _copy_input(args.agent_artifact, artifacts)
        mcp = _copy_input(args.mcp_artifact, artifacts)
        contracts_wheel = _copy_input(args.contracts_wheel, artifacts)
        if _wheel_version(contracts_wheel) != args.contracts_version:
            raise PackagingError("contracts wheel version does not match --contracts-version")
        contracts = json.loads(args.contracts.read_text(encoding="utf-8"))
        versions = {
            "platform": args.platform_version,
            "agent": args.agent_version,
            "plc-mcp": args.mcp_version,
        }
        bom = build_bom(
            product_release=args.product_release,
            components={
                component: {"artifact": str(path), "version": versions[component]}
                for component, path in (("platform", platform), ("agent", agent), ("plc-mcp", mcp))
            },
            contracts=contracts,
            contract_artifact={"artifact": str(contracts_wheel), "version": args.contracts_version},
        )
        bom_path = output / "controlstudio.bom.json"
        bom_path.write_text(json.dumps(bom, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        verify_bom(bom, artifacts)
        portable = assemble(bom_path, artifacts, output / "portable")
        shutil.copy2(ROOT / "scripts" / "Start-ControlStudio.ps1", portable / "Start-ControlStudio.ps1")
    except (OSError, json.JSONDecodeError, PackagingError) as exc:
        print(f"Release assembly refused: {exc}", file=sys.stderr)
        return 2
    print(portable)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
