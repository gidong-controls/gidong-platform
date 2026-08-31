from __future__ import annotations

import unittest

from controlstudio_host.agent_client import _runtime_diagnostic_from_payload


class AgentRuntimeDiagnosticTests(unittest.TestCase):
    def test_accepts_only_allowlisted_runtime_diagnostic(self) -> None:
        diagnostic = _runtime_diagnostic_from_payload(
            {
                "stage": "engineering_analysis",
                "exceptionKind": "usage_limit_exceeded",
                "reasonCode": "tool_call_budget",
            }
        )

        self.assertIsNotNone(diagnostic)
        assert diagnostic is not None
        self.assertEqual(diagnostic.stage, "engineering_analysis")
        self.assertEqual(diagnostic.exception_kind, "usage_limit_exceeded")
        self.assertEqual(diagnostic.reason_code, "tool_call_budget")

    def test_rejects_untrusted_runtime_detail(self) -> None:
        self.assertIsNone(
            _runtime_diagnostic_from_payload(
                {
                    "stage": "engineering_analysis",
                    "exceptionKind": "usage_limit_exceeded",
                    "reasonCode": "tool_call_budget",
                    "detail": "provider response text must not cross the boundary",
                }
            )
        )

    def test_accepts_allowlisted_tool_failure_without_detail(self) -> None:
        diagnostic = _runtime_diagnostic_from_payload(
            {
                "stage": "engineering_analysis",
                "exceptionKind": "tool_failure",
                "reasonCode": "tool_error",
            }
        )

        self.assertIsNotNone(diagnostic)
        assert diagnostic is not None
        self.assertEqual(diagnostic.exception_kind, "tool_failure")
        self.assertEqual(diagnostic.reason_code, "tool_error")


if __name__ == "__main__":
    unittest.main()
