import unittest

from controlstudio_host.agent_client import _SAFE_RUNTIME_FRAME_STDERR


class RuntimeFrameDiagnosticsTests(unittest.TestCase):
    def test_only_fixed_location_fields_cross_the_stderr_boundary(self):
        line = ("2026-09-09 12:37:00,123 WARNING controlstudio_agent.runtime "
                "runtime invariant at runtime frame validate_native_design line 12345 kind KeyError")
        match = _SAFE_RUNTIME_FRAME_STDERR.fullmatch(line + "\r\n")
        self.assertIsNotNone(match)
        self.assertEqual(match.groupdict(), {"function": "validate_native_design",
                                           "line": "12345", "kind": "KeyError"})
        for invalid in (line + " private provider data", line.replace("KeyError", "SecretError"),
                        line.replace("validate_native_design", "file/path"),
                        line.replace("12345", "-1"), line + "\nprivate data"):
            self.assertIsNone(_SAFE_RUNTIME_FRAME_STDERR.fullmatch(invalid))


if __name__ == "__main__":
    unittest.main()
