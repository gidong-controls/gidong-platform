from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from controlstudio_launcher.app import ControlStudioLauncher
from controlstudio_launcher.elevation_receipt import SCHEMA, ElevationReceiptStore
from controlstudio_launcher.health import (
    HealthResult,
    HealthState,
    LaunchVerificationResult,
)


class _Guard:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _TokenStore:
    def load(self) -> str:
        return "t" * 48


class _ReadyProbe:
    def probe(self) -> HealthResult:
        return HealthResult(HealthState.READY, "verified Host")

    def verify_launch_token(self, _token: str) -> LaunchVerificationResult:
        return LaunchVerificationResult(True, "verified", retryable=False)


class _AbsentThenReadyProbe:
    def __init__(self) -> None:
        self._states = iter((HealthState.ABSENT, HealthState.READY))

    def probe(self) -> HealthResult:
        state = next(self._states)
        return HealthResult(state, state.value)

    def verify_launch_token(self, _token: str) -> LaunchVerificationResult:
        return LaunchVerificationResult(True, "verified", retryable=False)


class _Broker:
    def __init__(self, responses: tuple[bool, ...]) -> None:
        self._responses = iter(responses)
        self.calls: list[tuple[bool, bool]] = []

    def relaunch_if_required(self, *, verified_host: bool, parent_opens_ui: bool) -> bool:
        self.calls.append((verified_host, parent_opens_ui))
        return next(self._responses)


class _RejectingBroker:
    def relaunch_if_required(self, *, verified_host: bool, parent_opens_ui: bool) -> bool:
        if verified_host:
            raise RuntimeError("verified listener is below required integrity")
        return False


class ElevationReceiptTests(unittest.TestCase):
    def _launcher(self, *, probe, broker, store: ElevationReceiptStore) -> ControlStudioLauncher:
        return ControlStudioLauncher(
            settings=SimpleNamespace(
                host_command=("host",),
                host_cwd=None,
                startup_timeout_seconds=5.0,
                poll_interval_seconds=0.01,
            ),
            health_probe=probe,
            host_starter=object(),
            ui_opener=object(),
            instance_guard=_Guard(),
            token_store=_TokenStore(),
            elevation_broker=broker,
            elevation_receipts=store,
            monotonic=lambda: 0.0,
            sleep=lambda _seconds: None,
        )

    def test_verified_followup_records_no_elevation_and_retention(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "elevation.json"
            broker = _Broker((False, False))
            self._launcher(
                probe=_ReadyProbe(), broker=broker, store=ElevationReceiptStore(path)
            ).run(open_ui=False)
            document = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(document["schema"], SCHEMA)
        [observation] = document["observations"]
        self.assertTrue(observation["initial_host_verified"])
        self.assertFalse(observation["elevation_relaunch_requested"])
        self.assertTrue(observation["host_ready"])
        self.assertTrue(observation["elevated_host_retention_verified"])
        self.assertEqual(observation["outcome"], "ready")
        self.assertEqual(broker.calls, [(True, False), (True, False)])

    def test_parent_handoff_binds_request_ready_and_retention(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "elevation.json"
            broker = _Broker((True, False))
            self._launcher(
                probe=_AbsentThenReadyProbe(),
                broker=broker,
                store=ElevationReceiptStore(path),
            ).run(open_ui=False)
            document = json.loads(path.read_text(encoding="utf-8"))

        [observation] = document["observations"]
        self.assertFalse(observation["initial_host_verified"])
        self.assertTrue(observation["elevation_relaunch_requested"])
        self.assertTrue(observation["host_ready"])
        self.assertTrue(observation["elevated_host_retention_verified"])
        self.assertEqual(observation["launcher_role"], "launcher")
        self.assertEqual(broker.calls, [(False, False), (True, False)])

    def test_failed_retention_is_durable_and_never_marked_ready(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "elevation.json"
            with self.assertRaisesRegex(RuntimeError, "below required integrity"):
                self._launcher(
                    probe=_ReadyProbe(),
                    broker=_RejectingBroker(),
                    store=ElevationReceiptStore(path),
                ).run(open_ui=False)
            document = json.loads(path.read_text(encoding="utf-8"))

        [observation] = document["observations"]
        self.assertEqual(observation["outcome"], "failed")
        self.assertFalse(observation["host_ready"])
        self.assertFalse(observation["elevated_host_retention_verified"])
