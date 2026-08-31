from __future__ import annotations

import unittest

from controlstudio_host.agent_client import _runtime_diagnostic_from_payload


class ProviderStatusDiagnosticsTests(unittest.TestCase):
    def test_host_accepts_only_bounded_http_failure_status(self) -> None:
        for code in ("provider_http_400", "provider_http_429", "provider_http_503"):
            with self.subTest(code=code):
                diagnostic = _runtime_diagnostic_from_payload(
                    {
                        "stage": "planning_native_design",
                        "exceptionKind": "model_api_error",
                        "reasonCode": code,
                    }
                )
                self.assertIsNotNone(diagnostic)
                self.assertEqual(diagnostic.reason_code, code)

    def test_host_rejects_status_with_arbitrary_provider_text(self) -> None:
        for code in (
            "provider_http_200",
            "provider_http_600",
            "provider_http_401 secret",
            "provider_http_٤٠١",
        ):
            with self.subTest(code=code):
                self.assertIsNone(
                    _runtime_diagnostic_from_payload(
                        {
                            "stage": "planning_native_design",
                            "exceptionKind": "model_api_error",
                            "reasonCode": code,
                        }
                    )
                )
