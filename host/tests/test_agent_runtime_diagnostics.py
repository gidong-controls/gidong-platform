from __future__ import annotations

import unittest

from controlstudio_host.agent_client import _runtime_diagnostic_from_payload
from controlstudio_host.agent_runtime import AgentRuntimeDiagnostic
from controlstudio_host.app import _agent_runtime_user_message


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

    def test_tool_failure_message_does_not_claim_read_or_no_mutation(self) -> None:
        for reason_code in ("tool_error", "transport"):
            with self.subTest(reason_code=reason_code):
                message = _agent_runtime_user_message(
                    AgentRuntimeDiagnostic(
                        stage="engineering_analysis",
                        exception_kind="tool_failure",
                        reason_code=reason_code,
                    )
                )
                self.assertEqual(
                    message,
                    "엔지니어링 도구 호출을 완료하지 못했습니다. 실행 기록을 확인해 주세요.",
                )
                self.assertNotIn("읽기", message)
                self.assertNotIn("실제 변경", message)


if __name__ == "__main__":
    unittest.main()
