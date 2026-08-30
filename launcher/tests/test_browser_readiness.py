from __future__ import annotations

import unittest
from types import SimpleNamespace

from controlstudio_launcher.app import ControlStudioLauncher
from controlstudio_launcher.health import (
    BrowserHandoffResult,
    BrowserReadinessResult,
)


class _Probe:
    def __init__(self) -> None:
        self.readiness_calls: list[tuple[str, str]] = []
        self._results = iter(
            (
                BrowserReadinessResult(
                    False,
                    "browser has not completed bootstrap yet",
                    retryable=True,
                ),
                BrowserReadinessResult(True, "verified browser bootstrap receipt"),
            )
        )

    def issue_browser_handoff(self, _launch_token: str) -> BrowserHandoffResult:
        return BrowserHandoffResult(
            True,
            "issued one-time browser handoff",
            "t" * 48,
            "r" * 48,
        )

    def probe_browser_readiness(
        self,
        launch_token: str,
        readiness_receipt: str,
    ) -> BrowserReadinessResult:
        self.readiness_calls.append((launch_token, readiness_receipt))
        return next(self._results)


class _Ui:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def open(self, url: str) -> None:
        self.urls.append(url)


class BrowserReadinessLauncherTests(unittest.TestCase):
    def test_new_window_waits_for_host_bootstrap_receipt(self) -> None:
        probe = _Probe()
        ui = _Ui()
        launcher = object.__new__(ControlStudioLauncher)
        launcher._health_probe = probe
        launcher._ui_opener = ui
        launcher._settings = SimpleNamespace(
            startup_timeout_seconds=5.0,
            poll_interval_seconds=0.1,
        )
        ticks = iter((0.0, 0.0, 1.0))
        launcher._monotonic = lambda: next(ticks)
        launcher._sleep = lambda _seconds: None

        launcher._open_ui("l" * 48, reuse_existing=False)

        self.assertEqual(len(ui.urls), 1)
        self.assertIn("#handoff_ticket=", ui.urls[0])
        self.assertEqual(probe.readiness_calls, [("l" * 48, "r" * 48)] * 2)


if __name__ == "__main__":
    unittest.main()
