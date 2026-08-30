from __future__ import annotations

"""Fail-closed durable observation journal for the CA-12 SSH live gate.

This module deliberately does not start, configure, or connect an SSH server.
An eventual privileged SSH transport adapter must call these methods only after
it independently observed the transport entry/disconnect and the separate
interactive Windows session. The Host then binds those observations to one
durable journal run. It produces an acceptance-shaped *draft* only; it never
claims a remote live PASS itself.
"""

import hashlib
import json
import threading
import uuid
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .store import HostStore

CA12_LIVE_RECEIPT_SCHEMA = "controlstudio-plc-live-gate-receipt/v1"
CA12_JOURNAL_SCHEMA = "controlstudio-ssh-interactive-handoff-journal/v1"
_STATE_PREFIX = "ssh_interactive_handoff/"
_SSH_TRANSPORT_SOURCE = "ssh_transport_adapter_v1"
_INTERACTIVE_SOURCE = "interactive_windows_session_adapter_v1"


class SshHandoffConflict(ValueError):
    """The submitted observation cannot extend the exact journal run."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _canonical_digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise SshHandoffConflict(f"{name} must be a non-empty string up to 256 characters")
    if any(character.isspace() or ord(character) < 33 for character in value):
        raise SshHandoffConflict(f"{name} contains an invalid character")
    return value


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise SshHandoffConflict(f"{name} must be a positive integer")
    return value


class SshInteractiveHandoffJournal:
    """Bind adapter-observed SSH and local-session evidence in Host storage."""

    def __init__(
        self,
        store: HostStore,
        *,
        now: Callable[[], str] = _utc_now,
    ) -> None:
        self._store = store
        self._now = now
        self._lock = threading.RLock()

    def record_ssh_entry(self, *, entry_id: str, source: str) -> str:
        """Start a run only from the dedicated SSH transport adapter."""

        entry_id = _text(entry_id, "entry_id")
        if source != _SSH_TRANSPORT_SOURCE:
            raise SshHandoffConflict("SSH entry must be observed by the transport adapter")
        with self._lock:
            run_id = str(uuid.uuid4())
            state = {
                "schema": CA12_JOURNAL_SCHEMA,
                "run_id": run_id,
                "created_at": self._now(),
                "ssh": {
                    "entry_id": entry_id,
                    "source": source,
                    "entered_at": self._now(),
                    "disconnected": False,
                },
                "interactive_windows_session": None,
                "disconnected_at": None,
            }
            self._save(state, "ssh_handoff.entry")
            return run_id

    def record_interactive_handoff(
        self,
        *,
        run_id: str,
        session_id: int,
        xg5000_pid: int,
        xg5000_hwnd: int,
        handoff_retained: bool,
        source: str,
    ) -> None:
        """Record the independently observed interactive Windows target."""

        run_id = _text(run_id, "run_id")
        if source != _INTERACTIVE_SOURCE:
            raise SshHandoffConflict("interactive session must be observed by its adapter")
        if type(handoff_retained) is not bool:
            raise SshHandoffConflict("handoff_retained must be a boolean")
        with self._lock:
            state = self._load(run_id)
            if state.get("interactive_windows_session") is not None:
                raise SshHandoffConflict("interactive handoff is already recorded")
            if state["ssh"]["disconnected"] is True:
                raise SshHandoffConflict("cannot bind an interactive session after SSH disconnect")
            state["interactive_windows_session"] = {
                "session_id": _positive_int(session_id, "session_id"),
                "xg5000_pid": _positive_int(xg5000_pid, "xg5000_pid"),
                "xg5000_hwnd": _positive_int(xg5000_hwnd, "xg5000_hwnd"),
                "handoff_retained": handoff_retained,
                "source": source,
                "observed_at": self._now(),
            }
            self._save(state, "ssh_handoff.interactive_bound")

    def record_ssh_disconnect(self, *, run_id: str, entry_id: str, source: str) -> None:
        """Close the exact transport entry after an interactive handoff exists."""

        run_id = _text(run_id, "run_id")
        entry_id = _text(entry_id, "entry_id")
        if source != _SSH_TRANSPORT_SOURCE:
            raise SshHandoffConflict("SSH disconnect must be observed by the transport adapter")
        with self._lock:
            state = self._load(run_id)
            if state["ssh"]["entry_id"] != entry_id:
                raise SshHandoffConflict("SSH disconnect entry_id does not match the journal")
            if state.get("interactive_windows_session") is None:
                raise SshHandoffConflict("cannot disconnect before interactive handoff is recorded")
            if state["ssh"]["disconnected"] is True:
                raise SshHandoffConflict("SSH disconnect is already recorded")
            state["ssh"]["disconnected"] = True
            state["ssh"]["disconnect_source"] = source
            state["disconnected_at"] = self._now()
            self._save(state, "ssh_handoff.disconnected")

    def acceptance_draft(self, *, run_id: str, manifest_sha256: str) -> dict[str, Any]:
        """Export CA-12-shaped evidence without promoting it to a live PASS."""

        run_id = _text(run_id, "run_id")
        if (
            not isinstance(manifest_sha256, str)
            or len(manifest_sha256) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in manifest_sha256)
        ):
            raise SshHandoffConflict("manifest_sha256 must be a SHA-256 digest")
        with self._lock:
            state = self._load(run_id)
        complete = self._is_complete(state)
        receipt: dict[str, Any] = {
            "schema": CA12_LIVE_RECEIPT_SCHEMA,
            "gate_id": "CA-12",
            "status": "NOT_RUN",
            "captured_at": self._now(),
            "manifest_sha256": manifest_sha256.casefold(),
            "reason": (
                "durable Host observation is complete but remote SSH live verification has not been performed"
                if complete
                else "durable Host observation is incomplete"
            ),
            "observation_complete": complete,
        }
        if complete:
            interactive = state["interactive_windows_session"]
            assert isinstance(interactive, dict)
            receipt["evidence"] = {
                "ssh": {"entry_id": state["ssh"]["entry_id"], "disconnected": True},
                "interactive_windows_session": {
                    "session_id": interactive["session_id"],
                    "xg5000_pid": interactive["xg5000_pid"],
                    "xg5000_hwnd": interactive["xg5000_hwnd"],
                    "handoff_retained": True,
                },
                "host_journal": {
                    "run_id": state["run_id"],
                    "preserved": True,
                    "schema": CA12_JOURNAL_SCHEMA,
                    "journal_sha256": _canonical_digest(state),
                    "source_assurance": "host_durable_ssh_handoff_observation_v1",
                },
            }
        return receipt

    def _load(self, run_id: str) -> dict[str, Any]:
        state = self._store.get_host_state(f"{_STATE_PREFIX}{run_id}")
        if not isinstance(state, dict) or state.get("schema") != CA12_JOURNAL_SCHEMA:
            raise SshHandoffConflict("SSH handoff journal run does not exist")
        if state.get("run_id") != run_id or not isinstance(state.get("ssh"), dict):
            raise SshHandoffConflict("SSH handoff journal is invalid")
        return deepcopy(state)

    def _save(self, state: dict[str, Any], event_type: str) -> None:
        run_id = state["run_id"]
        self._store.set_host_state(f"{_STATE_PREFIX}{run_id}", state)
        self._store.append_event(
            event_type=event_type,
            payload={
                "run_id": run_id,
                "entry_id": state["ssh"]["entry_id"],
                "journal_sha256": _canonical_digest(state),
            },
        )

    @staticmethod
    def _is_complete(state: dict[str, Any]) -> bool:
        interactive = state.get("interactive_windows_session")
        return bool(
            state.get("schema") == CA12_JOURNAL_SCHEMA
            and isinstance(state.get("ssh"), dict)
            and state["ssh"].get("disconnected") is True
            and state["ssh"].get("source") == _SSH_TRANSPORT_SOURCE
            and state["ssh"].get("disconnect_source") == _SSH_TRANSPORT_SOURCE
            and isinstance(interactive, dict)
            and interactive.get("source") == _INTERACTIVE_SOURCE
            and interactive.get("handoff_retained") is True
            and type(interactive.get("session_id")) is int
            and type(interactive.get("xg5000_pid")) is int
            and type(interactive.get("xg5000_hwnd")) is int
            and interactive["session_id"] > 0
            and interactive["xg5000_pid"] > 0
            and interactive["xg5000_hwnd"] > 0
        )
