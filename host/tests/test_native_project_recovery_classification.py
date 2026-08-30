from __future__ import annotations

import unittest

from controlstudio_host.native_projects import (
    _native_create_failed_before_confirmation,
    _native_create_failed_before_program_write,
    _retired_native_target_replaced_by_idle_instance,
)


class NativeProjectRecoveryClassificationTests(unittest.TestCase):
    def test_menu_invocation_failure_is_stage_ambiguous(self) -> None:
        self.assertFalse(
            _native_create_failed_before_confirmation(
                "project.create_native was rejected: MENU_INVOKE_FAILED: "
                "XG5000 command invocation failed"
            )
        )

    def test_clipboard_failure_is_after_empty_project_creation(self) -> None:
        self.assertFalse(
            _native_create_failed_before_confirmation(
                "project.create_native was rejected: "
                "XG5000_CLIPBOARD_SNAPSHOT_FAILED: snapshot helper did not start"
            )
        )

    def test_post_create_failure_is_not_downgraded(self) -> None:
        self.assertFalse(
            _native_create_failed_before_confirmation(
                "NATIVE_PROJECT_POST_CREATE_VERIFICATION_UNCERTAIN: reopen failed"
            )
        )

    def test_clipboard_snapshot_failure_precedes_program_write(self) -> None:
        self.assertTrue(
            _native_create_failed_before_program_write(
                "project.create_native was rejected: "
                "XG5000_CLIPBOARD_SNAPSHOT_FAILED: snapshot helper did not start"
            )
        )

    def test_program_readback_failure_may_follow_program_write(self) -> None:
        self.assertFalse(
            _native_create_failed_before_program_write(
                "NATIVE_BUILD_PROGRAM_READBACK_MISMATCH: readback differs"
            )
        )

    def test_retired_target_and_exact_idle_replacement_are_proven(self) -> None:
        self.assertTrue(
            _retired_native_target_replaced_by_idle_instance(
                {
                    "instances": [
                        {
                            "pid": 11308,
                            "hwnd": 395956,
                            "title": "XG5000",
                            "visible": True,
                        }
                    ]
                },
                stored_pid=2376,
                stored_hwnd=526020,
                selected_pid=11308,
                selected_hwnd=395956,
            )
        )

    def test_live_original_target_cannot_be_abandoned(self) -> None:
        self.assertFalse(
            _retired_native_target_replaced_by_idle_instance(
                {
                    "instances": [
                        {
                            "pid": 2376,
                            "hwnd": 526020,
                            "title": "UI 레더 수직검증 20260817 - XG5000",
                            "visible": True,
                        },
                        {
                            "pid": 11308,
                            "hwnd": 395956,
                            "title": "XG5000",
                            "visible": True,
                        },
                    ]
                },
                stored_pid=2376,
                stored_hwnd=526020,
                selected_pid=11308,
                selected_hwnd=395956,
            )
        )


if __name__ == "__main__":
    unittest.main()
