"""Build one user-facing ControlStudio setup EXE with Windows IExpress."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from controlstudio_packaging.bom import PackagingError, verify_bom


def _sed_text(
    *,
    source: Path,
    support_source: Path,
    target: Path,
    payload_files: list[str],
    support_files: list[str],
    release_channel: str,
) -> str:
    files = [*payload_files, *support_files]
    strings = "\n".join(f'FILE{index}="{name}"' for index, name in enumerate(files))
    payload_entries = "\n".join(
        f"%FILE{index}%=" for index in range(len(payload_files))
    )
    support_entries = "\n".join(
        f"%FILE{index}%=" for index in range(len(payload_files), len(files))
    )
    friendly_name = (
        "ControlStudio Beta Setup"
        if release_channel == "beta"
        else "ControlStudio Setup"
    )
    return f"""[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=0
HideExtractAnimation=0
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=
DisplayLicense=
FinishMessage=
TargetName={target}
FriendlyName={friendly_name}
AppLaunched=cmd.exe /D /C Install-ControlStudio.cmd
PostInstallCmd=<None>
AdminQuietInstCmd=cmd.exe /D /C Install-ControlStudio.cmd
UserQuietInstCmd=cmd.exe /D /C Install-ControlStudio.cmd
SourceFiles=SourceFiles
[Strings]
{strings}
[SourceFiles]
SourceFiles0={source}\\
SourceFiles1={support_source}\\
[SourceFiles0]
{payload_entries}
[SourceFiles1]
{support_entries}
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create the single ControlStudio consumer installer."
    )
    parser.add_argument("--portable", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--iexpress", type=Path, default=Path("C:/Windows/System32/iexpress.exe")
    )
    args = parser.parse_args()
    portable = args.portable.resolve()
    output = args.output.resolve()
    if output.exists():
        print(f"Installer build refused: output exists: {output}", file=sys.stderr)
        return 2
    iexpress = args.iexpress.resolve()
    if not iexpress.is_file():
        print(
            "Installer build refused: Windows IExpress is unavailable.", file=sys.stderr
        )
        return 2
    try:
        bom_path = portable / "controlstudio.bom.json"
        bom = json.loads(bom_path.read_text(encoding="utf-8"))
        release_channel = bom.get("release_channel")
        if release_channel == "development":
            raise PackagingError(
                "development releases are portable-only; build a beta candidate to install"
            )
        if release_channel not in {"beta", "production"}:
            raise PackagingError("installer release channel is invalid")
        verify_bom(
            bom,
            portable,
            require_production=release_channel == "production",
        )
        component_files = [item["artifact"] for item in bom["components"]]
        contract_file = bom["contract_artifact"]["artifact"]
        payload_files = [*component_files, contract_file, "controlstudio.bom.json"]
        for name in payload_files:
            if Path(name).name != name or not (portable / name).is_file():
                raise PackagingError(f"installer payload is missing or unsafe: {name}")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="controlstudio-installer-"
        ) as temporary:
            staging = Path(temporary)
            staged_output = staging / output.name
            support_files = [
                "Install-ControlStudio.cmd",
                "Install-ControlStudio.ps1",
                "Uninstall-ControlStudio.ps1",
                "Uninstall-ControlStudio.Core.psm1",
            ]
            for name in support_files:
                shutil.copy2(ROOT / "scripts" / name, staging / name)
            sed = staging / "ControlStudio.sed"
            sed.write_text(
                _sed_text(
                    source=portable,
                    support_source=staging,
                    target=staged_output,
                    payload_files=payload_files,
                    support_files=support_files,
                    release_channel=release_channel,
                ),
                encoding="utf-8",
            )
            completed = subprocess.run([str(iexpress), "/N", str(sed)], cwd=staging, check=False)
            if completed.returncode or not staged_output.is_file():
                raise PackagingError(
                    f"IExpress failed with exit code {completed.returncode}"
                )
            shutil.copy2(staged_output, output)
    except (OSError, json.JSONDecodeError, KeyError, PackagingError) as exc:
        print(f"Installer build refused: {exc}", file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
