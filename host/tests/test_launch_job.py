from __future__ import annotations

import ctypes
import unittest

from controlstudio_host.launch_job import (
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
    _JOB_OBJECT_LIMIT_BREAKAWAY_OK,
    _allow_verified_ide_breakaway,
)


class _SetInformationJobObject:
    def __init__(self) -> None:
        self.arguments: tuple[object, ...] | None = None

    def __call__(self, *arguments: object) -> int:
        self.arguments = arguments
        return 1


class _Kernel32:
    def __init__(self) -> None:
        self.SetInformationJobObject = _SetInformationJobObject()


class LaunchJobLifetimeTests(unittest.TestCase):
    def test_existing_named_job_is_idempotently_enabled_for_verified_ide_breakaway(self) -> None:
        kernel32 = _Kernel32()

        _allow_verified_ide_breakaway(kernel32, 123)

        arguments = kernel32.SetInformationJobObject.arguments
        self.assertIsNotNone(arguments)
        assert arguments is not None
        self.assertEqual(arguments[0].value, 123)
        self.assertEqual(arguments[1], _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION)
        raw = ctypes.string_at(arguments[2], arguments[3])
        # JOB_OBJECT_BASIC_LIMIT_INFORMATION starts with two LARGE_INTEGER
        # values, then its DWORD LimitFlags.
        self.assertEqual(
            int.from_bytes(raw[16:20], byteorder="little"),
            _JOB_OBJECT_LIMIT_BREAKAWAY_OK,
        )


if __name__ == "__main__":
    unittest.main()
