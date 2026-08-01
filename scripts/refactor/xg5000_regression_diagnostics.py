"""Create a read-only XG5000 regression-gate diagnostic record.

This command deliberately does *not* start XG5000, connect to a PLC, or start
an MCP server.  It only inventories the local replay prerequisites and checks
the historical JSON evidence used by the refactor gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_FILES = (
    "artifacts/ld-read-e2e/result-capability-final.json",
    "artifacts/ld-change-e2e/result-standalone-rollback.json",
)
COMMON_INSTALL_DIRS = (
    Path(r"C:\Program Files\LSIS"),
    Path(r"C:\Program Files (x86)\LSIS"),
    Path(r"C:\Program Files\LS ELECTRIC"),
    Path(r"C:\Program Files (x86)\LS ELECTRIC"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _xg5000_processes() -> list[dict[str, str]]:
    """Read the Windows process table without opening or controlling an IDE."""
    completed = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq XG5000.EXE"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    processes: list[dict[str, str]] = []
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("INFO:"):
            continue
        fields = [part.strip('"') for part in line.split('","')]
        if len(fields) >= 2 and fields[0].upper() == "XG5000.EXE":
            processes.append({"image": fields[0], "pid": fields[1]})
    return processes


def _evidence_summary(relative_path: str) -> dict[str, Any]:
    path = REPO_ROOT / relative_path
    result: dict[str, Any] = {
        "path": relative_path,
        "exists": path.is_file(),
    }
    if not path.is_file():
        return result
    result["sha256"] = _sha256(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result["parse_error"] = str(exc)
        return result

    # Preserve only facts needed to establish the prior gate's limited scope.
    result["facts"] = {
        key: payload.get(key)
        for key in (
            "ok",
            "transport",
            "project_path",
            "project_sha256_before",
            "project_sha256_after",
            "project_unchanged",
            "program_count",
            "total_visual_rows",
            "final_status",
        )
    }
    rollback = payload.get("rollback")
    if isinstance(rollback, dict):
        result["rollback"] = {
            key: rollback.get(key)
            for key in (
                "operation",
                "ok",
                "verified",
                "status",
                "matches_original",
                "matches_original_canonical",
            )
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Ignored artifact JSON destination (default: timestamped refactor artifact).",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    output = args.output or (
        REPO_ROOT
        / "artifacts"
        / "refactor-xg5000"
        / f"diagnostics-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    path_executable = shutil.which("XG5000.exe")
    process_list = _xg5000_processes()
    configured_historical_project = os.environ.get(
        "CONTROLSTUDIO_HISTORICAL_XG5000_WORKING_COPY", ""
    ).strip()
    historical_project = (
        Path(configured_historical_project).expanduser().resolve()
        if configured_historical_project
        else REPO_ROOT / "artifacts" / "historical-working-copy.xgwx"
    )
    simulator_fixture = REPO_ROOT / "artifacts" / "CodexXG5000E2E.xgwx"

    reasons: list[str] = []
    if not path_executable:
        reasons.append("XG5000.exe is not discoverable on PATH.")
    if not process_list:
        reasons.append("No XG5000 process is currently available for a read-only attach.")
    if not historical_project.is_file():
        reasons.append(
            "The historical working-copy path is absent; its evidence cannot be replayed."
        )
    reasons.append(
        "No operator-designated offline working copy/PID/HWND was supplied; mutation harnesses remain forbidden."
    )

    report = {
        "schema": "controlstudio.refactor.xg5000-regression-diagnostics/v1",
        "generated_at": now.isoformat(timespec="seconds"),
        "mode": "read_only_diagnostics",
        "live_operations_executed": [],
        "environment": {
            "xg5000_on_path": path_executable,
            "xg5000_processes": process_list,
            "common_install_directories": [
                {"path": str(path), "exists": path.is_dir()} for path in COMMON_INSTALL_DIRS
            ],
            "historical_working_copy": {
                "path": str(historical_project),
                "exists": historical_project.is_file(),
            },
            "simulator_fixture": {
                "path": str(simulator_fixture.relative_to(REPO_ROOT)),
                "exists": simulator_fixture.is_file(),
                "sha256": _sha256(simulator_fixture) if simulator_fixture.is_file() else None,
            },
            "safe_replay_eligible": False,
            "replay_blockers": reasons,
        },
        "historical_evidence": [_evidence_summary(path) for path in EVIDENCE_FILES],
        "harness_assessment": {
            "private PLC MCP run_ld_read_e2e.py": {
                "classification": "read_only_when_analysis_profile_is_enforced",
                "known_calls": [
                    "xg5000.attach",
                    "project.list_programs",
                    "program.read_ld",
                    "program.logic_graph",
                    "program.state_model",
                    "device.references",
                    "project.behavior_report",
                    "evidence.get",
                ],
                "negative_authorization_probe": "project.backup must be denied to analysis",
            },
            "private PLC MCP run_ld_change_e2e.py": {
                "classification": "offline_mutation_and_rollback; NOT run by this gate",
                "reason": "enables unsafe local validation profiles and calls apply/save/rollback.",
            },
            "private PLC MCP run_ls_adapter_e2e.py": {
                "classification": "XG-SIM mutation harness; NOT run by this gate",
                "reason": "opens an IDE working copy and writes/runs the simulator.",
            },
        },
        "online_plc_scope": {
            "executed": False,
            "verified": False,
            "prohibited_operations": [
                "PLC write",
                "force",
                "mode change",
                "download",
                "online run edit",
            ],
        },
        "gate_result": {
            "status": "NOT_EXECUTED",
            "meaning": "Historical evidence was inspected, but no current real-XG5000 replay was safe or eligible.",
        },
    }
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
