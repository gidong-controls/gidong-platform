from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from controlstudio_launcher.ui import wait_for_single_controlstudio_window


class BrowserOwnershipWaitTests(unittest.TestCase):
    def test_edge_uia_is_not_a_launch_readiness_dependency(self) -> None:
        """A verified app HWND may proceed to the Host receipt probe without UIA."""

        with (
            patch(
                "controlstudio_launcher.ui._controlstudio_windows",
                return_value=(101,),
            ),
            patch(
                "controlstudio_launcher.ui._set_launcher_owned_marker",
                return_value=True,
            ),
            patch("controlstudio_launcher.ui._focus_controlstudio_window"),
        ):
            self.assertTrue(
                wait_for_single_controlstudio_window(
                    Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
                    timeout_seconds=1,
                    monotonic=lambda: 0,
                )
            )


if __name__ == "__main__":
    unittest.main()
