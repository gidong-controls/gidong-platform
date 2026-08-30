from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class SourceDevelopmentEntrypointTests(unittest.TestCase):
    def test_source_helper_uses_canonical_launcher_job_starter(self) -> None:
        workspace = Path(__file__).resolve().parents[3]
        script = workspace / "artifacts" / "start-controlstudio-user.py"
        module_name = "controlstudio_source_host_helper_test"
        spec = importlib.util.spec_from_file_location(module_name, script)
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        helper = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = helper
        spec.loader.exec_module(helper)
        launched: list[tuple[tuple[str, ...], Path, dict[str, str]]] = []
        released: list[object] = []
        stopped: list[object] = []
        process = object()

        class Starter:
            def start(
                self,
                command: tuple[str, ...],
                cwd: Path,
                environment: dict[str, str],
            ) -> object:
                launched.append((command, cwd, environment))
                return process

        launcher_host = types.ModuleType("controlstudio_launcher.host")
        launcher_host.SubprocessHostStarter = Starter
        launcher_host.release_process_started_by_launcher = released.append
        launcher_host.stop_process_started_by_launcher = stopped.append
        app_data = Path(tempfile.mkdtemp())
        try:
            (app_data / "ControlStudio Development").mkdir()
            (app_data / "ControlStudio Development" / "session.token").write_text(
                "t" * 32,
                encoding="utf-8",
            )
            probes = iter((False, True))
            helper.APP_DATA = app_data
            helper._port_open = lambda _port: next(probes)
            with patch.dict(
                sys.modules,
                {"controlstudio_launcher.host": launcher_host},
            ):
                self.assertEqual(helper.main(), 0)
        finally:
            sys.modules.pop(module_name, None)
            for child in sorted(app_data.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            app_data.rmdir()

        self.assertEqual(len(launched), 1)
        self.assertEqual(launched[0][1], workspace / "gidong-platform")
        self.assertEqual(launched[0][0][1:], ("-m", "controlstudio_host"))
        self.assertEqual(released, [process])
        self.assertEqual(stopped, [])


if __name__ == "__main__":
    unittest.main()
