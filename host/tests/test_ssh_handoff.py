from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from controlstudio_host.ssh_handoff import (
    CA12_JOURNAL_SCHEMA,
    SshHandoffConflict,
    SshInteractiveHandoffJournal,
)
from controlstudio_host.store import HostStore


class SshInteractiveHandoffJournalTests(unittest.TestCase):
    manifest_sha256 = "a" * 64

    def _journal(self, root: Path) -> SshInteractiveHandoffJournal:
        return SshInteractiveHandoffJournal(HostStore(root / "host.sqlite3"))

    def test_durable_complete_observation_exports_not_run_acceptance_draft(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            journal = self._journal(root)
            run_id = journal.record_ssh_entry(
                entry_id="ssh-entry-1", source="ssh_transport_adapter_v1"
            )
            journal.record_interactive_handoff(
                run_id=run_id,
                session_id=1,
                xg5000_pid=100,
                xg5000_hwnd=200,
                handoff_retained=True,
                source="interactive_windows_session_adapter_v1",
            )
            journal.record_ssh_disconnect(
                run_id=run_id,
                entry_id="ssh-entry-1",
                source="ssh_transport_adapter_v1",
            )
            draft = journal.acceptance_draft(
                run_id=run_id, manifest_sha256=self.manifest_sha256
            )
            events = HostStore(root / "host.sqlite3").list_events(limit=100)

        self.assertEqual(draft["status"], "NOT_RUN")
        self.assertTrue(draft["observation_complete"])
        self.assertEqual(
            draft["evidence"]["ssh"],
            {"entry_id": "ssh-entry-1", "disconnected": True},
        )
        self.assertEqual(draft["evidence"]["host_journal"]["run_id"], run_id)
        self.assertEqual(draft["evidence"]["host_journal"]["schema"], CA12_JOURNAL_SCHEMA)
        self.assertEqual(
            [event.event_type for event in events if event.payload.get("run_id") == run_id],
            ["ssh_handoff.entry", "ssh_handoff.interactive_bound", "ssh_handoff.disconnected"],
        )

    def test_restart_reloads_run_and_rejects_wrong_or_duplicate_disconnect(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            journal = self._journal(root)
            run_id = journal.record_ssh_entry(
                entry_id="ssh-entry-2", source="ssh_transport_adapter_v1"
            )
            restarted = self._journal(root)
            restarted.record_interactive_handoff(
                run_id=run_id,
                session_id=3,
                xg5000_pid=101,
                xg5000_hwnd=201,
                handoff_retained=True,
                source="interactive_windows_session_adapter_v1",
            )
            with self.assertRaisesRegex(SshHandoffConflict, "does not match"):
                restarted.record_ssh_disconnect(
                    run_id=run_id,
                    entry_id="other-entry",
                    source="ssh_transport_adapter_v1",
                )
            restarted.record_ssh_disconnect(
                run_id=run_id,
                entry_id="ssh-entry-2",
                source="ssh_transport_adapter_v1",
            )
            with self.assertRaisesRegex(SshHandoffConflict, "already recorded"):
                restarted.record_ssh_disconnect(
                    run_id=run_id,
                    entry_id="ssh-entry-2",
                    source="ssh_transport_adapter_v1",
                )

    def test_incomplete_or_unretained_observation_never_exports_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            journal = self._journal(Path(raw))
            run_id = journal.record_ssh_entry(
                entry_id="ssh-entry-3", source="ssh_transport_adapter_v1"
            )
            journal.record_interactive_handoff(
                run_id=run_id,
                session_id=4,
                xg5000_pid=102,
                xg5000_hwnd=202,
                handoff_retained=False,
                source="interactive_windows_session_adapter_v1",
            )
            journal.record_ssh_disconnect(
                run_id=run_id,
                entry_id="ssh-entry-3",
                source="ssh_transport_adapter_v1",
            )
            draft = journal.acceptance_draft(
                run_id=run_id, manifest_sha256=self.manifest_sha256
            )

        self.assertEqual(draft["status"], "NOT_RUN")
        self.assertFalse(draft["observation_complete"])
        self.assertNotIn("evidence", draft)

    def test_untrusted_adapter_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            journal = self._journal(Path(raw))
            with self.assertRaisesRegex(SshHandoffConflict, "transport adapter"):
                journal.record_ssh_entry(entry_id="ssh-entry-4", source="browser")

    def test_invalid_manifest_digest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            journal = self._journal(Path(raw))
            run_id = journal.record_ssh_entry(
                entry_id="ssh-entry-5", source="ssh_transport_adapter_v1"
            )
            with self.assertRaisesRegex(SshHandoffConflict, "SHA-256"):
                journal.acceptance_draft(run_id=run_id, manifest_sha256="z" * 64)
