from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from controlstudio_packaging.bom import PackagingError, assemble


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble a portable, artifact-only ControlStudio release.")
    parser.add_argument("--bom", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-production-signature", action="store_true")
    args = parser.parse_args()
    try:
        output = assemble(args.bom, args.artifacts, args.output, require_production=args.require_production_signature)
    except PackagingError as exc:
        print(f"Portable assembly refused: {exc}", file=sys.stderr)
        return 2
    (output / "Start-ControlStudio.ps1").write_text((ROOT / "scripts" / "Start-ControlStudio.ps1").read_text(encoding="utf-8"), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
