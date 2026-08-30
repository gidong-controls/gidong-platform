from __future__ import annotations

import unittest

from controlstudio_host.agent_client import _output_contract_diagnostic_from_payload


class AgentOutputDiagnosticTests(unittest.TestCase):
    def test_accepts_every_engineering_continuation_validator_code(self) -> None:
        for code in (
            "continuation_approval",
            "continuation_citation",
            "continuation_contract",
            "continuation_mission_binding",
            "continuation_no_progress",
            "continuation_structural_binding",
        ):
            with self.subTest(code=code):
                diagnostic = _output_contract_diagnostic_from_payload(
                    {
                        "stage": "engineering_analysis",
                        "exceptionKind": "model_retry",
                        "reasonCode": "validator_rejected",
                        "validatorCode": code,
                        "validationErrors": [],
                    }
                )

                self.assertIsNotNone(diagnostic)
                assert diagnostic is not None
                self.assertEqual(diagnostic.validator_code, code)


if __name__ == "__main__":
    unittest.main()
