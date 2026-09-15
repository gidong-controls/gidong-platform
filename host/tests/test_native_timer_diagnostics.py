import unittest
from unittest.mock import patch

from controlstudio_host.native_projects import (
    NativeProjectPreconditionError,
    _compile_native_program,
)


class NativeTimerDiagnosticsTests(unittest.TestCase):
    def test_wrong_base_reports_required_base_and_preserves_duration(self):
        for series, address, expected in (
            ("XGK", "T0010", 100), ("XGK", "T1000", 10),
            ("XGB", "T010", 100),
        ):
            with self.subTest(series=series, address=address):
                artifact = self.artifact(address)
                with patch("controlstudio_host.native_projects._resolve_native_collection_scope", return_value={}):
                    with self.assertRaises(NativeProjectPreconditionError) as raised:
                        _compile_native_program(artifact, program_name="MAIN", plc_series=series)
                self.assertIn(f"requires timeBaseMs={expected}", str(raised.exception))
                self.assertIn("Preserve durationMs", str(raised.exception))
                self.assertEqual(artifact["design"]["timers"][0]["durationMs"], 5000)

    def test_out_of_range_address_does_not_suggest_a_valid_time_base(self):
        for series, address in (("XGK", "T2000"), ("XGB", "T256")):
            with self.subTest(series=series):
                with patch("controlstudio_host.native_projects._resolve_native_collection_scope", return_value={}):
                    with self.assertRaises(NativeProjectPreconditionError) as raised:
                        _compile_native_program(self.artifact(address), program_name="MAIN", plc_series=series)
                self.assertIn("choose a timer address", str(raised.exception))
                self.assertNotIn("requires timeBaseMs", str(raised.exception))

    @staticmethod
    def artifact(address):
        # Isolate timer compilation from the independently tested scope validator.
        design = {name: [] for name in (
            "ioPoints", "operatingModes", "controlNetworks", "sequences",
            "alarms", "recoveryRules", "safetyRules",
        )}
        design.update(schemaVersion="2", ladderRungs=[{}], timers=[{
            "timerId": "timeout", "address": address, "instruction": "TON",
            "timeBaseMs": 1, "presetTicks": 5000, "durationMs": 5000,
        }])
        return {"technicalConfiguration": {"cpuModel": "test"}, "design": design}
