from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from controlstudio_launcher.errors import ElevationError
from controlstudio_launcher.integrity import (
    ELEVATION_HANDOFF_ENVIRONMENT,
    WindowsElevationBroker,
    apply_elevation_context,
    encode_elevation_context,
)


class ElevationContextTests(unittest.TestCase):
    def test_fixed_context_round_trip_preserves_profile_and_sidecars_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            source = {
                "LOCALAPPDATA": raw,
                "CONTROLSTUDIO_RELEASE_CHANNEL": "development",
                "CONTROLSTUDIO_PRODUCT_RELEASE": "source-dev",
                "CONTROLSTUDIO_AGENT_COMMAND": "agent.exe --stdio",
                "CONTROLSTUDIO_MCP_COMMAND": "mcp.exe",
                "CONTROLSTUDIO_XG5000_CLI_COMMAND": "xg-cli.exe",
                "CONTROLSTUDIO_DEVELOPMENT_MODEL_API_KEY": "must-not-cross-command-line",
                "UNRELATED": "blocked",
            }
            encoded = encode_elevation_context(source)
            restored: dict[str, str] = {}
            apply_elevation_context(encoded, restored)

        self.assertEqual(restored["LOCALAPPDATA"], str(Path(raw).resolve()))
        self.assertEqual(restored["CONTROLSTUDIO_MCP_COMMAND"], "mcp.exe")
        self.assertEqual(restored["CONTROLSTUDIO_XG5000_CLI_COMMAND"], "xg-cli.exe")
        self.assertEqual(restored[ELEVATION_HANDOFF_ENVIRONMENT], "1")
        self.assertNotIn("CONTROLSTUDIO_DEVELOPMENT_MODEL_API_KEY", restored)
        self.assertNotIn("UNRELATED", restored)

    def test_context_rejects_unknown_environment_names(self) -> None:
        encoded = encode_elevation_context({"LOCALAPPDATA": str(Path.cwd())})
        import base64
        import json

        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        payload["environment"]["SYSTEMROOT"] = "C:/Windows"
        forged = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("ascii")
        ).decode("ascii").rstrip("=")
        with self.assertRaises(ElevationError):
            apply_elevation_context(forged, {})

    def test_broker_passes_one_context_to_elevated_child(self) -> None:
        relaunched: list[tuple[str, ...]] = []
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ,
            {
                "LOCALAPPDATA": raw,
                "CONTROLSTUDIO_RELEASE_CHANNEL": "development",
                "CONTROLSTUDIO_MCP_COMMAND": "mcp.exe",
            },
            clear=False,
        ):
            broker = WindowsElevationBroker(
                ("--no-config", "--no-open"),
                process_integrity=lambda _pid: 0x2000,
                xg5000_processes=lambda: (),
                installed_xg5000_target=lambda _integrity: 0x3000,
                relaunch=lambda arguments: relaunched.append(tuple(arguments)),
            )
            self.assertTrue(
                broker.relaunch_if_required(verified_host=False, parent_opens_ui=False)
            )
            self.assertEqual(len(relaunched), 1)
            arguments = relaunched[0]
            self.assertEqual(arguments.count("--elevation-context"), 1)
            restored: dict[str, str] = {}
            apply_elevation_context(
                arguments[arguments.index("--elevation-context") + 1], restored
            )
            self.assertEqual(restored["CONTROLSTUDIO_MCP_COMMAND"], "mcp.exe")
            self.assertEqual(restored["LOCALAPPDATA"], str(Path(raw).resolve()))


if __name__ == "__main__":
    unittest.main()
