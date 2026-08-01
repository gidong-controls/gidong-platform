from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from controlstudio_packaging.bom import PackagingError, verify_bom


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail closed unless a ControlStudio BOM matches its artifacts.")
    parser.add_argument("--bom", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--require-production-signature", action="store_true")
    args = parser.parse_args()
    try:
        verify_bom(json.loads(args.bom.read_text(encoding="utf-8")), args.artifacts, require_production=args.require_production_signature)
    except (OSError, json.JSONDecodeError, PackagingError) as exc:
        print(f"BOM verification refused: {exc}", file=sys.stderr)
        return 2
    print("BOM verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
