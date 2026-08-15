from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from controlstudio_packaging.bom import PackagingError, build_bom


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a deterministic ControlStudio v1 BOM."
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="JSON component input (artifact paths are local only).",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--development-signing-key",
        help="Local HMAC key; never a production publisher signature.",
    )
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        bom = build_bom(
            product_release=data["product_release"],
            release_channel=data["release_channel"],
            components=data["components"],
            contracts=data["contracts"],
            contract_artifact=data["contract_artifact"],
            signing=data.get("signing", "unsigned"),
            signing_key=args.development_signing_key,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(bom, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (KeyError, OSError, json.JSONDecodeError, PackagingError) as exc:
        print(f"BOM creation refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
