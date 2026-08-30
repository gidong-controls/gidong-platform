#!/usr/bin/env python3
"""Run deterministic PLC-Agent acceptance checks and fail closed on missing live evidence.

The runner never starts XG5000, asks for elevation, opens a browser, or connects over
SSH.  Those non-deterministic environments are represented by signed-in evidence files
provided later with --live-receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA = "controlstudio-plc-agent-acceptance-manifest/v1"
LIVE_RECEIPT_SCHEMA = "controlstudio-plc-live-gate-receipt/v1"
MAX_LOG_BYTES = 2 * 1024 * 1024


class ManifestError(ValueError):
    """The acceptance manifest is malformed or attempts an unsafe execution shape."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"could not read JSON file {path}") from exc
    if not isinstance(value, dict):
        raise ManifestError(f"JSON file {path} must contain an object")
    return value


def _inside(root: Path, candidate: Path) -> Path:
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ManifestError(f"path escapes repository root: {candidate}") from exc
    return resolved


def _load_manifest(path: Path, repository_root: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    manifest = _read_json(path)
    if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("version") != 1:
        raise ManifestError("unsupported PLC-Agent acceptance manifest schema/version")
    commands = manifest.get("deterministic_commands")
    live_gates = manifest.get("live_gates")
    if not isinstance(commands, list) or not commands:
        raise ManifestError("manifest needs one or more deterministic_commands")
    if not isinstance(live_gates, list) or not live_gates:
        raise ManifestError("manifest needs one or more live_gates")
    identifiers: set[str] = set()
    for item in commands:
        if not isinstance(item, dict):
            raise ManifestError("every deterministic command must be an object")
        identifier, cwd, command = item.get("id"), item.get("cwd"), item.get("command")
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise ManifestError("deterministic command ids must be unique non-empty strings")
        identifiers.add(identifier)
        if not isinstance(cwd, str) or not cwd or not _inside(repository_root, repository_root / cwd).is_dir():
            raise ManifestError(f"command {identifier} has an invalid cwd")
        if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command):
            raise ManifestError(f"command {identifier} must be a non-empty argv list")
        if not isinstance(item.get("ca_ids"), list) or not all(isinstance(x, str) and x.startswith("CA-") for x in item["ca_ids"]):
            raise ManifestError(f"command {identifier} must declare CA ids")
        timeout = item.get("timeout_seconds")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 900:
            raise ManifestError(f"command {identifier} timeout_seconds must be 1..900")
    gate_ids: set[str] = set()
    for gate in live_gates:
        if not isinstance(gate, dict) or not isinstance(gate.get("id"), str) or not gate["id"]:
            raise ManifestError("every live gate needs a non-empty id")
        if gate["id"] in gate_ids:
            raise ManifestError("live gate ids must be unique")
        gate_ids.add(gate["id"])
    if manifest.get("live_gate_receipt_schema") != LIVE_RECEIPT_SCHEMA:
        raise ManifestError("manifest must declare the exact live gate receipt schema")
    return manifest, _sha256_bytes(raw)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _snapshot_capture_log(capture: Any, destination: Path) -> dict[str, Any]:
    """Atomically freeze a bounded receipt log from an anonymous capture file.

    ``npm.cmd`` can return successfully while a descendant still owns inherited
    stdout/stderr handles.  The receipt must describe the command's completed
    root process, not a mutable stream that descendant may write later.  We
    therefore capture in ``TemporaryFile`` (delete-on-last-close on Windows),
    then copy the bytes present at root exit into an atomically replaced receipt
    log.  A later descendant write cannot change the receipt path or digest.

    We deliberately do *not* run ``taskkill /T`` after a successful root exit:
    on Windows that tool cannot reliably traverse a parent that no longer
    exists, and it could terminate an unrelated process after PID reuse.  The
    anonymous capture handle is released automatically when any inherited child
    exits; timeout cleanup remains process-group/tree based.
    """
    capture.flush()
    capture.seek(0)
    encoded = capture.read(MAX_LOG_BYTES + 1)
    truncated = len(encoded) > MAX_LOG_BYTES
    if truncated:
        encoded = encoded[:MAX_LOG_BYTES]
    staged = destination.with_name(f".{destination.name}.snapshot")
    try:
        staged.write_bytes(encoded)
        os.replace(staged, destination)
    finally:
        # If replace failed, do not leave a partially named receipt log around.
        if staged.exists():
            staged.unlink()
    return {"path": str(destination), "sha256": _sha256_bytes(encoded), "truncated": truncated}


def _finalize_existing_log(path: Path) -> dict[str, Any]:
    """Bound a log created before a command could acquire capture handles."""
    with path.open("rb") as source:
        encoded = source.read(MAX_LOG_BYTES + 1)
    truncated = len(encoded) > MAX_LOG_BYTES
    if truncated:
        encoded = encoded[:MAX_LOG_BYTES]
        path.write_bytes(encoded)
    return {"path": str(path), "sha256": _sha256_bytes(encoded), "truncated": truncated}


def _process_group_kwargs() -> dict[str, Any]:
    """Start each command in an isolated group for bounded timeout cleanup."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _stop_timed_out_command(process: subprocess.Popen[bytes]) -> None:
    """Best-effort termination of the command and its children after timeout."""
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            # /T prevents a timed-out npm.cmd from leaving node.exe behind.
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        # The command may have exited between poll() and termination.  The
        # direct-process fallback below still makes the lifecycle bounded.
        pass
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        # There is no safe additional action available to the harness.  The
        # receipt remains a timeout failure and never reports a false pass.
        pass


def _run_command(item: dict[str, Any], repository_root: Path, artifact_dir: Path, dry_run: bool) -> dict[str, Any]:
    identifier = str(item["id"])
    cwd = _inside(repository_root, repository_root / str(item["cwd"]))
    argv = list(item["command"])
    executable = Path(argv[0])
    if not executable.is_absolute() and (cwd / executable).is_file():
        # subprocess resolves an argv[0] path before it applies cwd on Windows.
        # Make manifest-local virtualenv executables unambiguous.
        argv[0] = str((cwd / executable).resolve())
    receipt: dict[str, Any] = {
        "id": identifier,
        "ca_ids": list(item["ca_ids"]),
        "argv": argv,
        "cwd": str(cwd),
        "timeout_seconds": item["timeout_seconds"],
    }
    if dry_run:
        receipt["status"] = "NOT_RUN"
        receipt["reason"] = "dry_run"
        return receipt
    started = time.monotonic()
    log_dir = artifact_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{identifier}.stdout.txt"
    stderr_path = log_dir / f"{identifier}.stderr.txt"
    # Do not collect command output through PIPE: a descendant holding an
    # inherited pipe after npm itself exits must not make a completed check time
    # out.  TemporaryFile is anonymous/delete-on-last-close on Windows, so it
    # also avoids a mutable orphan log after we atomically snapshot the receipt.
    try:
        with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(mode="w+b") as stderr_file:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                shell=False,
                stdout=stdout_file,
                stderr=stderr_file,
                **_process_group_kwargs(),
            )
            try:
                receipt["returncode"] = process.wait(timeout=item["timeout_seconds"])
                receipt["status"] = "PASS" if receipt["returncode"] == 0 else "FAIL"
            except subprocess.TimeoutExpired:
                _stop_timed_out_command(process)
                receipt["status"] = "FAIL"
                receipt["reason"] = "timeout"
            receipt["stdout"] = _snapshot_capture_log(stdout_file, stdout_path)
            receipt["stderr"] = _snapshot_capture_log(stderr_file, stderr_path)
    except subprocess.TimeoutExpired:
        # Kept for a defensive compatibility path if a platform-specific
        # Popen implementation raises outside the inner wait().
        receipt["status"] = "FAIL"
        receipt["reason"] = "timeout"
    except OSError as exc:
        receipt["status"] = "FAIL"
        receipt["reason"] = f"launch_error:{type(exc).__name__}"
        stderr_path.write_text(str(exc), encoding="utf-8", errors="replace")
    receipt["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    # Launch failures happen before Popen opens either capture file.
    stdout_path.touch(exist_ok=True)
    stderr_path.touch(exist_ok=True)
    if "stdout" not in receipt:
        receipt["stdout"] = _finalize_existing_log(stdout_path)
    if "stderr" not in receipt:
        receipt["stderr"] = _finalize_existing_log(stderr_path)
    return receipt


def _parse_live_receipts(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        gate, separator, raw_path = value.partition("=")
        if not separator or not gate or not raw_path or gate in result:
            raise ManifestError("--live-receipt must be unique GATE_ID=PATH")
        result[gate] = Path(raw_path)
    return result


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{name} must be a non-empty string")
    return value


def _required_bool(value: object, name: str, expected: bool = True) -> None:
    if type(value) is not bool or value is not expected:
        raise ManifestError(f"{name} must be exactly {str(expected).lower()}")


def _required_positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ManifestError(f"{name} must be a positive integer")
    return value


def _required_sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value.lower()):
        raise ManifestError(f"{name} must be a SHA-256 hex digest")
    return value.lower()


def _required_object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{name} must be an object")
    return value


def _required_timestamp(value: object, name: str) -> None:
    text = _required_text(value, name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManifestError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ManifestError(f"{name} must include a timezone offset")


def _validate_gate_evidence(gate_id: str, evidence: object) -> None:
    """Reject plausible-looking receipts unless their gate-specific proof exists."""
    value = _required_object(evidence, "evidence")
    if gate_id == "CA-01":
        _required_bool(value.get("first_launch_ready"), "first_launch_ready")
        if type(value.get("uac_prompt_count")) is not int or value["uac_prompt_count"] != 1:
            raise ManifestError("uac_prompt_count must be exactly integer 1")
        if type(value.get("subsequent_prompt_count")) is not int or value["subsequent_prompt_count"] != 0:
            raise ManifestError("subsequent_prompt_count must be exactly integer 0")
        return
    if gate_id == "CA-02":
        _required_positive_int(value.get("pid"), "pid")
        _required_positive_int(value.get("hwnd"), "hwnd")
        _required_text(value.get("project_path"), "project_path")
        _required_sha256(value.get("project_sha256"), "project_sha256")
        _required_text(value.get("program_id"), "program_id")
        _required_text(value.get("program_name"), "program_name")
        read_ld = _required_object(value.get("program_read_ld"), "program_read_ld")
        if read_ld.get("operation") != "program.read_ld":
            raise ManifestError("program_read_ld.operation must be program.read_ld")
        _required_bool(read_ld.get("verified"), "program_read_ld.verified")
        _required_sha256(read_ld.get("payload_sha256"), "program_read_ld.payload_sha256")
        return
    if gate_id == "CA-04-CA-07":
        _required_text(value.get("approval_id"), "approval_id")
        _required_sha256(value.get("approval_plan_sha256"), "approval_plan_sha256")
        _required_sha256(value.get("result_digest"), "result_digest")
        source = _required_object(value.get("source"), "source")
        reopened = _required_object(value.get("reopened"), "reopened")
        source_pid = _required_positive_int(source.get("pid"), "source.pid")
        source_hwnd = _required_positive_int(source.get("hwnd"), "source.hwnd")
        reopened_pid = _required_positive_int(reopened.get("pid"), "reopened.pid")
        reopened_hwnd = _required_positive_int(reopened.get("hwnd"), "reopened.hwnd")
        if source_pid == reopened_pid or source_hwnd == reopened_hwnd:
            raise ManifestError("reopened PID and HWND must each differ from the source")
        source_path = Path(_required_text(source.get("project_path"), "source.project_path")).resolve()
        reopened_path = Path(_required_text(reopened.get("project_path"), "reopened.project_path")).resolve()
        if str(source_path).casefold() != str(reopened_path).casefold():
            raise ManifestError("source and reopened project paths must be canonically identical")
        _required_sha256(value.get("project_hash_before"), "project_hash_before")
        project_hash_after = _required_sha256(value.get("project_hash_after"), "project_hash_after")
        reopened_project_sha256 = _required_sha256(reopened.get("project_sha256"), "reopened.project_sha256")
        if project_hash_after != reopened_project_sha256:
            raise ManifestError("project_hash_after must equal reopened.project_sha256")
        _required_bool(value.get("saved"), "saved")
        program_check = _required_object(value.get("program_check"), "program_check")
        if program_check.get("status") != "PASS":
            raise ManifestError("program_check.status must be PASS")
        _required_bool(program_check.get("passed"), "program_check.passed")
        readback = _required_object(value.get("readback"), "readback")
        # A vertical change may be either a full MNE+LD edit or an exact LD
        # operand edit.  Do not fabricate an MNE digest for an LD-only run:
        # the kind field keeps the receipt semantically strict.
        readback_kind = readback.get("kind", "MNE_AND_LD")
        if readback_kind == "LD":
            for field in ("before_ld_sha256", "after_ld_sha256"):
                _required_sha256(readback.get(field), f"readback.{field}")
            if readback["before_ld_sha256"].lower() == readback["after_ld_sha256"].lower():
                raise ManifestError("LD readback digest pair must change")
            return
        if readback_kind != "MNE_AND_LD":
            raise ManifestError("readback.kind must be LD or MNE_AND_LD")
        for field in ("before_mne_sha256", "after_mne_sha256", "before_ld_sha256", "after_ld_sha256"):
            _required_sha256(readback.get(field), f"readback.{field}")
        if (
            readback["before_mne_sha256"].lower() == readback["after_mne_sha256"].lower()
            and readback["before_ld_sha256"].lower() == readback["after_ld_sha256"].lower()
        ):
            raise ManifestError("at least one MNE or LD readback digest pair must change")
        return
    if gate_id == "CA-12":
        ssh = _required_object(value.get("ssh"), "ssh")
        _required_text(ssh.get("entry_id"), "ssh.entry_id")
        _required_bool(ssh.get("disconnected"), "ssh.disconnected")
        interactive = _required_object(value.get("interactive_windows_session"), "interactive_windows_session")
        _required_positive_int(interactive.get("session_id"), "interactive_windows_session.session_id")
        _required_positive_int(interactive.get("xg5000_pid"), "interactive_windows_session.xg5000_pid")
        _required_positive_int(interactive.get("xg5000_hwnd"), "interactive_windows_session.xg5000_hwnd")
        _required_bool(interactive.get("handoff_retained"), "interactive_windows_session.handoff_retained")
        journal = _required_object(value.get("host_journal"), "host_journal")
        _required_text(journal.get("run_id"), "host_journal.run_id")
        _required_bool(journal.get("preserved"), "host_journal.preserved")
        if journal.get("schema") != "controlstudio-ssh-interactive-handoff-journal/v1":
            raise ManifestError("host_journal.schema must be the durable SSH handoff schema")
        _required_sha256(journal.get("journal_sha256"), "host_journal.journal_sha256")
        if journal.get("source_assurance") != "host_durable_ssh_handoff_observation_v1":
            raise ManifestError("host_journal.source_assurance is invalid")
        return
    raise ManifestError(f"no strict evidence contract exists for {gate_id}")


def _live_gate_receipt(gate: dict[str, Any], manifest_sha256: str, supplied: Path | None) -> dict[str, Any]:
    gate_id = str(gate["id"])
    result: dict[str, Any] = {"id": gate_id, "title": gate.get("title"), "status": "NOT_RUN"}
    if supplied is None:
        result["reason"] = "external live receipt was not supplied"
        return result
    try:
        receipt = _read_json(supplied)
    except ManifestError as exc:
        result.update({"status": "FAIL_CLOSED", "reason": str(exc)})
        return result
    evidence = receipt.get("evidence")
    if (
        receipt.get("schema") != LIVE_RECEIPT_SCHEMA
        or receipt.get("gate_id") != gate_id
        or receipt.get("status") != "PASS"
        or receipt.get("manifest_sha256") != manifest_sha256
        or not isinstance(evidence, dict)
        or not evidence
    ):
        result.update({"status": "FAIL_CLOSED", "reason": "live receipt does not meet the exact gate contract"})
        return result
    try:
        _required_timestamp(receipt.get("captured_at"), "captured_at")
        _validate_gate_evidence(gate_id, evidence)
    except ManifestError as exc:
        result.update({"status": "FAIL_CLOSED", "reason": str(exc)})
        return result
    result.update({"status": "PASS", "receipt_path": str(supplied.resolve()), "receipt_sha256": _sha256_bytes(supplied.read_bytes())})
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_suffix(".manifest.json"))
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--live-receipt", action="append", default=[], metavar="GATE_ID=PATH")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--deterministic-only", action="store_true")
    args = parser.parse_args(argv)
    repository_root = Path(__file__).resolve().parents[2]
    try:
        manifest_path = args.manifest.resolve()
        manifest, manifest_sha256 = _load_manifest(manifest_path, repository_root)
        supplied = _parse_live_receipts(args.live_receipt)
        known_gates = {str(item["id"]) for item in manifest["live_gates"]}
        if set(supplied) - known_gates:
            raise ManifestError("a --live-receipt named an unknown live gate")
        default_root = repository_root / "artifacts" / "plc-agent-acceptance"
        root = (args.artifacts or default_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        artifact_dir = Path(tempfile.mkdtemp(prefix="run-", dir=root))
        command_receipts = [_run_command(item, repository_root, artifact_dir, args.dry_run) for item in manifest["deterministic_commands"]]
        deterministic_status = "PASS" if command_receipts and all(item["status"] == "PASS" for item in command_receipts) else "FAIL"
        live_receipts = [_live_gate_receipt(gate, manifest_sha256, supplied.get(str(gate["id"]))) for gate in manifest["live_gates"]]
        live_status = "PASS" if all(item["status"] == "PASS" for item in live_receipts) else "FAIL_CLOSED"
        overall = "PASS" if deterministic_status == "PASS" and live_status == "PASS" else ("FAIL" if deterministic_status == "FAIL" else "FAIL_CLOSED")
        receipt = {
            "schema": "controlstudio-plc-agent-acceptance-receipt/v1",
            "generated_at": _utc_now(),
            "manifest_path": str(manifest_path),
            "manifest_sha256": manifest_sha256,
            "execution_mode": "dry_run" if args.dry_run else ("deterministic_only" if args.deterministic_only else "full"),
            "deterministic_status": deterministic_status,
            "live_status": live_status,
            "overall_status": overall,
            "commands": command_receipts,
            "live_gates": live_receipts,
            "safety": {"xg5000_ui_started": False, "uac_requested": False, "ssh_connected": False},
        }
        _write_json(artifact_dir / "receipt.json", receipt)
        print(json.dumps({"artifact_dir": str(artifact_dir), "overall_status": overall, "deterministic_status": deterministic_status, "live_status": live_status}, ensure_ascii=False, sort_keys=True))
        if args.dry_run or (args.deterministic_only and deterministic_status == "PASS") or overall == "PASS":
            return 0
        return 1
    except (ManifestError, OSError) as exc:
        print(json.dumps({"schema": "controlstudio-plc-agent-acceptance-error/v1", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
