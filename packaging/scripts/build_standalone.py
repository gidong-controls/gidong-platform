"""Build one-file Windows executables without a runtime Python, Node, or uv."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENTRY_POINT = REPO / "packaging" / "entrypoints" / "platform_main.py"
IMPORT_PATHS = [
    REPO / "launcher" / "src",
    REPO / "host" / "src",
    REPO / "packages" / "python" / "controlstudio-contracts" / "src",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the public Platform standalone EXE using PyInstaller.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pyinstaller", default="pyinstaller")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    executable = shutil.which(args.pyinstaller) or (args.pyinstaller if Path(args.pyinstaller).is_file() else None)
    if not executable:
        print("Standalone build refused: PyInstaller is not installed in the build environment.", file=sys.stderr)
        return 2
    args.output.mkdir(parents=True, exist_ok=True)
    work = args.output / ".pyinstaller-work"
    if not (REPO / "web" / "dist" / "index.html").is_file():
        print("Standalone build refused: web/dist/index.html is required for a portable Platform UI.", file=sys.stderr)
        return 2
    command = [executable, "--noconfirm", "--onefile", "--name", "controlstudio-platform", "--distpath", str(args.output), "--workpath", str(work / "platform"), "--specpath", str(work / "spec"), "--copy-metadata", "fastmcp-slim"]
    if args.clean:
        command.append("--clean")
    for import_path in IMPORT_PATHS:
        command.extend(["--paths", str(import_path)])
    command.extend(["--add-data", f"{REPO / 'web' / 'dist'}{os.pathsep}web-dist", str(ENTRY_POINT)])
    return subprocess.run(command, cwd=REPO, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
