from __future__ import annotations

import copy
import unittest

from controlstudio_host.agent_client import _safe_trusted_context
from controlstudio_host.native_engineering_status import (
    derive_native_engineering_status,
    validate_native_engineering_status,
)


def _handoff() -> dict[str, object]:
    return {
        "schemaVersion": "1",
        "nativeProjectId": "native-project",
        "planningConversationId": "planning-conversation",
        "planningMessageId": "planning-message",
        "reviewedPlanDigest": "1" * 64,
        "projectBriefDigest": "2" * 64,
        "attachmentRevisionDigest": "3" * 64,
        "buildBundleId": "build-bundle",
        "buildReceiptSchemaVersion": "controlstudio-native-build-receipt/v3",
        "buildReceiptDigest": "4" * 64,
        "reopenReceiptSchemaVersion": "controlstudio-native-reopen-receipt/v2",
        "reopenReceiptDigest": "5" * 64,
        "projectSha256": "6" * 64,
    }


def _status() -> dict[str, object]:
    return derive_native_engineering_status(
        handoff=_handoff(),
        reviewed_plan={
            "schemaVersion": "2",
            "objective": "Validate the reviewed conveyor control objective.",
            "sections": [
                {
                    "sectionId": "overview",
                    "items": [
                        {"verificationMethod": "Inspect the reviewed LD behavior."},
                        {"verificationMethod": "Inspect the reviewed LD behavior."},
                    ],
                },
                {
                    "sectionId": "validation",
                    "items": [
                        {"verificationMethod": "Verify interlock coverage."},
                    ],
                },
            ],
        },
        project_brief={
            "performance_and_acceptance": "Meet the explicit 500 ms response limit."
        },
    )


class NativeEngineeringStatusTests(unittest.TestCase):
    def test_projection_is_sanitized_deterministic_and_forwarded_exactly(self) -> None:
        status = _status()

        self.assertEqual(
            status["completionCriteria"],
            [
                "Inspect the reviewed LD behavior.",
                "Verify interlock coverage.",
                "Meet the explicit 500 ms response limit.",
            ],
        )
        self.assertEqual(
            status["completedStages"],
            [
                "reviewed_plan",
                "native_build",
                "independent_reopen",
            ],
        )
        self.assertEqual(status["remainingStages"], ["engineering_validation"])
        self.assertNotIn("planningConversationId", status)
        self.assertNotIn("acceptedByActorId", status)
        self.assertNotIn("projectPath", status)

        trusted = _safe_trusted_context(
            {"workflow": "engineering", "native_engineering_status": status},
            knowledge_search_available=True,
            change_planner_available=False,
            mne_change_planner_available=False,
        )
        assert trusted is not None
        self.assertEqual(trusted["native_engineering_status"], status)

    def test_drop_extra_and_digest_tampering_fail_closed(self) -> None:
        status = _status()
        invalid_values: list[dict[str, object]] = []

        dropped = copy.deepcopy(status)
        dropped.pop("projectSha256")
        invalid_values.append(dropped)

        invalid_values.append({**status, "rawReceipt": {"secret": True}})
        invalid_values.append({**status, "objective": "replacement objective"})
        invalid_values.append(
            {**status, "completedStages": ["reviewed_plan", "native_build"]}
        )

        for invalid in invalid_values:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_native_engineering_status(invalid)
                with self.assertRaises(ValueError):
                    _safe_trusted_context(
                        {
                            "workflow": "engineering",
                            "native_engineering_status": invalid,
                        },
                        knowledge_search_available=False,
                        change_planner_available=False,
                        mne_change_planner_available=False,
                    )

    def test_ordinary_engineering_context_is_unchanged(self) -> None:
        trusted = _safe_trusted_context(
            {"workflow": "engineering", "stage": "ANALYSIS_READY"},
            knowledge_search_available=False,
            change_planner_available=False,
            mne_change_planner_available=False,
        )
        self.assertEqual(
            trusted,
            {
                "workflow": "engineering",
                "knowledge_search_available": False,
                "change_planner_available": False,
                "mne_change_planner_available": False,
                "stage": "ANALYSIS_READY",
            },
        )


if __name__ == "__main__":
    unittest.main()
