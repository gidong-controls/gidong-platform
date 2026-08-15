"""Build one-file Windows executables without a runtime Python, Node, or uv."""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENTRY_POINT = REPO / "packaging" / "entrypoints" / "platform_main.py"
PYWIN32_RUNTIME_HOOK = REPO / "packaging" / "runtime_hooks" / "pywin32_path.py"
IMPORT_PATHS = [
    REPO / "launcher" / "src",
    REPO / "host" / "src",
    REPO / "packages" / "python" / "controlstudio-contracts" / "src",
]
REQUIRED_RUNTIME_MODULES = (
    "pywinauto",
    "pywinauto.keyboard",
    "win32timezone",
)


def missing_required_runtime_modules() -> tuple[str, ...]:
    missing = []
    for module in REQUIRED_RUNTIME_MODULES:
        try:
            available = importlib.util.find_spec(module) is not None
        except ModuleNotFoundError:
            available = False
        if not available:
            missing.append(module)
    return tuple(missing)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create the public Platform standalone EXE using PyInstaller."
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pyinstaller", default="pyinstaller")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    missing_modules = missing_required_runtime_modules()
    if missing_modules:
        print(
            "Standalone build refused: launcher runtime module(s) missing: "
            + ", ".join(missing_modules),
            file=sys.stderr,
        )
        return 2
    executable = shutil.which(args.pyinstaller) or (
        args.pyinstaller if Path(args.pyinstaller).is_file() else None
    )
    if not executable:
        print(
            "Standalone build refused: PyInstaller is not installed in the build environment.",
            file=sys.stderr,
        )
        return 2
    args.output.mkdir(parents=True, exist_ok=True)
    work = args.output / ".pyinstaller-work"
    if not (REPO / "web" / "dist" / "index.html").is_file():
        print(
            "Standalone build refused: web/dist/index.html is required for a portable Platform UI.",
            file=sys.stderr,
        )
        return 2
    command = [
        executable,
        "--noconfirm",
        "--onefile",
        "--uac-admin",
        "--name",
        "controlstudio-platform",
        "--distpath",
        str(args.output),
        "--workpath",
        str(work / "platform"),
        "--specpath",
        str(work / "spec"),
        "--copy-metadata",
        "fastmcp-slim",
        "--runtime-hook",
        str(PYWIN32_RUNTIME_HOOK),
    ]
    for module in REQUIRED_RUNTIME_MODULES:
        command.extend(["--hidden-import", module])
    if args.clean:
        command.append("--clean")
    for import_path in IMPORT_PATHS:
        command.extend(["--paths", str(import_path)])
    command.extend(["--add-data", f"{REPO / 'web' / 'dist'}{os.pathsep}web-dist", str(ENTRY_POINT)])
    return subprocess.run(command, cwd=REPO, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
