from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "plc_agent_acceptance.py"
SPEC = importlib.util.spec_from_file_location("plc_agent_acceptance", SCRIPT)
assert SPEC and SPEC.loader
HARNESS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HARNESS
SPEC.loader.exec_module(HARNESS)


class PlcAgentAcceptanceTests(unittest.TestCase):
    def _manifest(self, directory: Path) -> Path:
        manifest = {
            "schema": HARNESS.MANIFEST_SCHEMA,
            "version": 1,
            "deterministic_commands": [{
                "id": "deterministic-proof",
                "ca_ids": ["CA-03"],
                "cwd": ".",
                "command": [sys.executable, "-c", "print('deterministic proof')"],
                "timeout_seconds": 10,
            }],
            "live_gate_receipt_schema": HARNESS.LIVE_RECEIPT_SCHEMA,
            "live_gates": [{"id": "CA-12", "title": "SSH live gate"}],
        }
        path = directory / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def test_deterministic_only_generates_fail_closed_live_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._manifest(root)
            artifacts = root / "artifacts"
            self.assertEqual(HARNESS.main(["--manifest", str(manifest), "--artifacts", str(artifacts), "--deterministic-only"]), 0)
            receipt = json.loads(next(artifacts.glob("run-*/receipt.json")).read_text(encoding="utf-8"))
        self.assertEqual(receipt["deterministic_status"], "PASS")
        self.assertEqual(receipt["live_status"], "FAIL_CLOSED")
        self.assertEqual(receipt["overall_status"], "FAIL_CLOSED")
        self.assertEqual(receipt["live_gates"][0]["status"], "NOT_RUN")
        self.assertFalse(receipt["safety"]["xg5000_ui_started"])
        self.assertEqual(receipt["commands"][0]["argv"][0], sys.executable)

    def test_invalid_live_receipt_can_never_promote_a_run_to_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._manifest(root)
            artifacts = root / "artifacts"
            live = root / "live.json"
            live.write_text(json.dumps({"schema": HARNESS.LIVE_RECEIPT_SCHEMA, "gate_id": "CA-12", "status": "PASS", "evidence": {"fake": True}}), encoding="utf-8")
            self.assertEqual(HARNESS.main(["--manifest", str(manifest), "--artifacts", str(artifacts), "--live-receipt", f"CA-12={live}"]), 1)
            receipt = json.loads(next(artifacts.glob("run-*/receipt.json")).read_text(encoding="utf-8"))
        self.assertEqual(receipt["overall_status"], "FAIL_CLOSED")
        self.assertEqual(receipt["live_gates"][0]["status"], "FAIL_CLOSED")

    def test_superficially_valid_live_receipt_is_rejected_without_gate_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._manifest(root)
            artifacts = root / "artifacts"
            live = root / "live.json"
            live.write_text(
                json.dumps(
                    {
                        "schema": HARNESS.LIVE_RECEIPT_SCHEMA,
                        "gate_id": "CA-12",
                        "status": "PASS",
                        "captured_at": "2026-08-25T00:00:00+00:00",
                        "manifest_sha256": HARNESS._sha256_bytes(manifest.read_bytes()),
                        "evidence": {"ssh": "present"},
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                HARNESS.main(
                    ["--manifest", str(manifest), "--artifacts", str(artifacts), "--live-receipt", f"CA-12={live}"]
                ),
                1,
            )
            receipt = json.loads(next(artifacts.glob("run-*/receipt.json")).read_text(encoding="utf-8"))
        self.assertEqual(receipt["live_gates"][0]["status"], "FAIL_CLOSED")
        self.assertIn("ssh must be an object", receipt["live_gates"][0]["reason"])

    def test_complete_strict_live_receipt_can_complete_the_harness(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._manifest(root)
            artifacts = root / "artifacts"
            live = root / "live.json"
            live.write_text(
                json.dumps(
                    {
                        "schema": HARNESS.LIVE_RECEIPT_SCHEMA,
                        "gate_id": "CA-12",
                        "status": "PASS",
                        "captured_at": "2026-08-25T00:00:00+00:00",
                        "manifest_sha256": HARNESS._sha256_bytes(manifest.read_bytes()),
                        "evidence": {
                            "ssh": {"entry_id": "ssh-entry-1", "disconnected": True},
                            "interactive_windows_session": {
                                "session_id": 1,
                                "xg5000_pid": 100,
                                "xg5000_hwnd": 200,
                                "handoff_retained": True,
                            },
                            "host_journal": {
                                "run_id": "run-1",
                                "preserved": True,
                                "schema": "controlstudio-ssh-interactive-handoff-journal/v1",
                                "journal_sha256": "a" * 64,
                                "source_assurance": "host_durable_ssh_handoff_observation_v1",
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                HARNESS.main(
                    ["--manifest", str(manifest), "--artifacts", str(artifacts), "--live-receipt", f"CA-12={live}"]
                ),
                0,
            )
            receipt = json.loads(next(artifacts.glob("run-*/receipt.json")).read_text(encoding="utf-8"))
        self.assertEqual(receipt["overall_status"], "PASS")
        self.assertEqual(receipt["live_gates"][0]["status"], "PASS")

    def test_mne_live_gate_requires_two_new_window_identities_and_bound_digests(self) -> None:
        digest = "a" * 64
        changed = "b" * 64
        evidence = {
            "approval_id": "approval-1",
            "approval_plan_sha256": digest,
            "result_digest": changed,
            "source": {"pid": 100, "hwnd": 200, "project_path": "C:/work/project.xgwx"},
            "reopened": {
                "pid": 101,
                "hwnd": 201,
                "project_path": "c:/WORK/project.xgwx",
                "project_sha256": changed,
            },
            "project_hash_before": digest,
            "project_hash_after": changed,
            "saved": True,
            "program_check": {"status": "PASS", "passed": True},
            "readback": {
                "before_mne_sha256": digest,
                "after_mne_sha256": changed,
                "before_ld_sha256": digest,
                "after_ld_sha256": changed,
            },
        }
        HARNESS._validate_gate_evidence("CA-04-CA-07", evidence)
        evidence["reopened"]["pid"] = 100
        with self.assertRaisesRegex(HARNESS.ManifestError, "PID and HWND"):
            HARNESS._validate_gate_evidence("CA-04-CA-07", evidence)
        evidence["reopened"]["pid"] = 101
        evidence["reopened"]["project_sha256"] = digest
        with self.assertRaisesRegex(HARNESS.ManifestError, "project_hash_after"):
            HARNESS._validate_gate_evidence("CA-04-CA-07", evidence)

    def test_ld_only_live_gate_requires_a_changed_ld_pair_without_fabricated_mne_hashes(self) -> None:
        before = "c" * 64
        after = "d" * 64
        evidence = {
            "approval_id": "approval-ld-1",
            "approval_plan_sha256": before,
            "result_digest": after,
            "source": {"pid": 100, "hwnd": 200, "project_path": "C:/work/project.xgwx"},
            "reopened": {
                "pid": 101,
                "hwnd": 201,
                "project_path": "C:/work/project.xgwx",
                "project_sha256": after,
            },
            "project_hash_before": before,
            "project_hash_after": after,
            "saved": True,
            "program_check": {"status": "PASS", "passed": True},
            "readback": {
                "kind": "LD",
                "before_ld_sha256": before,
                "after_ld_sha256": after,
            },
        }
        HARNESS._validate_gate_evidence("CA-04-CA-07", evidence)
        evidence["readback"]["after_ld_sha256"] = before
        with self.assertRaisesRegex(HARNESS.ManifestError, "LD readback digest pair must change"):
            HARNESS._validate_gate_evidence("CA-04-CA-07", evidence)

    def test_completed_command_is_not_held_hostage_by_descendant_pipe_handles(self) -> None:
        """A background child inheriting stdout must not turn parent success into timeout.

        This mirrors npm/vite on Windows: npm has completed, but an unrelated
        descendant still owns the inherited stdout pipe.  The acceptance runner
        writes logs to files and waits for the command process only.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_dir = root / "artifacts"
            command = {
                "id": "inherited-handle",
                "ca_ids": ["CA-03"],
                "cwd": ".",
                "command": [
                    sys.executable,
                    "-c",
                    (
                        "import subprocess, sys; "
                        "subprocess.Popen([sys.executable, '-c', \"import time; time.sleep(3); print('late child output')\"]); "
                        "print('parent complete')"
                    ),
                ],
                "timeout_seconds": 2,
            }
            started = time.monotonic()
            receipt = HARNESS._run_command(command, root, artifact_dir, dry_run=False)
            elapsed = time.monotonic() - started
            stdout = Path(receipt["stdout"]["path"]).read_text(encoding="utf-8")
            # The child deliberately inherited the two log files.  Let it
            # release those Windows handles before TemporaryDirectory cleanup.
            time.sleep(3.2)
            stdout_after_child_exit = Path(receipt["stdout"]["path"]).read_text(encoding="utf-8")
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["returncode"], 0)
        self.assertLess(elapsed, 2)
        self.assertIn("parent complete", stdout)
        self.assertNotIn("late child output", stdout)
        self.assertEqual(stdout_after_child_exit, stdout)


if __name__ == "__main__":
    unittest.main()
