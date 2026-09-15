import asyncio
import unittest
from types import SimpleNamespace

from controlstudio_host.agent_client import _SAFE_NATIVE_RESPONSE_STDERR, AgentSupervisor


class NativeResponseDiagnosticsTests(unittest.TestCase):
    def test_exact_numeric_shape_only(self):
        line = (
            "2026-09-10 09:00:00,123 WARNING controlstudio_agent.runtime "
            "native response shape finish=length output_tokens=12000 text_chars=43210"
        )
        self.assertIsNotNone(_SAFE_NATIVE_RESPONSE_STDERR.fullmatch(line + "\r\n"))
        for invalid in (
            line + " SECRET", line.replace("length", "PRIVATE_VALUE"),
            line.replace("12000", "-1"), line.replace("12000", "1234567890"),
            line.replace("43210", "NaN"), line + "\nSECRET",
        ):
            self.assertIsNone(_SAFE_NATIVE_RESPONSE_STDERR.fullmatch(invalid))

    def test_stderr_drain_retains_shape_but_not_arbitrary_text(self):
        async def exercise():
            stream = asyncio.StreamReader()
            stream.feed_data(
                b"2026-09-10 09:00:00,123 WARNING controlstudio_agent.runtime "
                b"native response shape finish=length output_tokens=12000 text_chars=43210\n"
                b"PRIVATE_VALUE arbitrary endpoint response\n"
            )
            stream.feed_eof()
            supervisor = object.__new__(AgentSupervisor)
            await supervisor._drain_stderr(SimpleNamespace(stderr=stream))

        with self.assertLogs("controlstudio_host.agent_client", level="WARNING") as captured:
            asyncio.run(exercise())
        output = "\n".join(captured.output)
        self.assertIn("finish=length output_tokens=12000 text_chars=43210", output)
        self.assertNotIn("PRIVATE_VALUE", output)
        self.assertNotIn("endpoint response", output)


if __name__ == "__main__":
    unittest.main()
