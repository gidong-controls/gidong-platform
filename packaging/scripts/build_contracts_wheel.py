"""Build the public contracts wheel owned by the Platform repository."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
CONTRACTS = REPOSITORY / "packages" / "python" / "controlstudio-contracts"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the public controlstudio-contracts wheel.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not (CONTRACTS / "pyproject.toml").is_file():
        print("Contracts wheel build refused: public contracts package is missing.", file=sys.stderr)
        return 2
    args.output.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        ["uv", "build", "--directory", str(CONTRACTS), "--out-dir", str(args.output)],
        cwd=REPOSITORY,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
