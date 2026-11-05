"""Build the public contracts wheel owned by the Platform repository."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

REPOSITORY = Path(__file__).resolve().parents[2]
CONTRACTS = REPOSITORY / "packages" / "python" / "controlstudio-contracts"
PACKAGE = "controlstudio_contracts"


def _source_inputs() -> dict[str, bytes]:
    package = CONTRACTS / "src" / PACKAGE
    inputs = {"pyproject.toml": (CONTRACTS / "pyproject.toml").read_bytes()}
    for path in sorted(package.rglob("*")):
        relative = path.relative_to(package)
        if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("public contracts source contains a linked path")
        if path.is_file():
            inputs[(Path("src") / PACKAGE / relative).as_posix()] = path.read_bytes()
    if f"src/{PACKAGE}/__init__.py" not in inputs:
        raise ValueError("public contracts source package is missing")
    return inputs


def _verify_wheel(path: Path, inputs: dict[str, bytes]) -> None:
    expected = {name.removeprefix("src/"): value for name, value in inputs.items()
                if name.startswith(f"src/{PACKAGE}/")}
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
        if len(names) != len(set(names)):
            raise ValueError("contracts wheel contains duplicate ZIP members")
        if any("\\" in name or PurePosixPath(name).is_absolute()
               or ".." in PurePosixPath(name).parts for name in names):
            raise ValueError("contracts wheel contains an invalid member path")
        files = {item.filename for item in wheel.infolist() if not item.is_dir()}
        package_files = {name for name in files if name.startswith(PACKAGE + "/")}
        if package_files != set(expected):
            raise ValueError("contracts wheel package inventory differs from current source")
        if any(wheel.read(name) != value for name, value in expected.items()):
            raise ValueError("contracts wheel package bytes differ from current source")
        metadata_roots = {PurePosixPath(name).parts[0] for name in files - package_files}
        if (len(metadata_roots) != 1
                or not next(iter(metadata_roots)).startswith(PACKAGE + "-")
                or not next(iter(metadata_roots)).endswith(".dist-info")):
            raise ValueError("contracts wheel must contain one distribution metadata directory")
        metadata_root = next(iter(metadata_roots))
        if not {f"{metadata_root}/{name}" for name in ("METADATA", "WHEEL", "RECORD")} <= files:
            raise ValueError("contracts wheel distribution metadata is incomplete")
        metadata = BytesParser().parsebytes(wheel.read(metadata_root + "/METADATA"))
        if (str(metadata.get("Name", "")).replace("-", "_") != PACKAGE
                or not metadata.get("Version")
                or metadata_root != f"{PACKAGE}-{metadata['Version']}.dist-info"):
            raise ValueError("contracts wheel distribution metadata does not match the package")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the public controlstudio-contracts wheel.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    if not (CONTRACTS / "pyproject.toml").is_file():
        print("Contracts wheel build refused: public contracts package is missing.", file=sys.stderr)
        return 2
    try:
        inputs = _source_inputs()
        args.output.mkdir(parents=True, exist_ok=True)
        # Setuptools must never reuse checkout build/lib or egg-info/SOURCES.
        # Only the current source package and its project definition enter this
        # newly owned stage; original caches and outputs remain untouched.
        with tempfile.TemporaryDirectory(prefix="cs-contracts-") as temporary:
            stage, output = Path(temporary) / "source", Path(temporary) / "dist"
            for relative, data in inputs.items():
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            completed = subprocess.run(
                ["uv", "build", "--directory", str(stage), "--out-dir", str(output)],
                cwd=REPOSITORY,
                check=False,
            )
            if completed.returncode:
                return completed.returncode
            artifacts = sorted(output.iterdir())
            wheels = [path for path in artifacts if path.suffix == ".whl"]
            if len(wheels) != 1 or any(not path.is_file() or path.is_symlink() for path in artifacts):
                raise ValueError("contracts build did not produce one regular wheel")
            _verify_wheel(wheels[0], inputs)
            if _source_inputs() != inputs:
                raise ValueError("public contracts source changed during the build")
            for artifact in artifacts:
                shutil.copy2(artifact, args.output / artifact.name)
        return 0
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"Contracts wheel build refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
