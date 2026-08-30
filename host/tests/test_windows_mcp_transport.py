from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client

from controlstudio_host import mcp_client, windows_mcp_transport


def _windows_process_is_alive(pid: int) -> bool:
    """Read only the owned fixture process exit state without shelling out."""

    if sys.platform != "win32":
        return False
    import win32api
    import win32con
    import win32process

    try:
        handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    except win32api.error:
        return False
    try:
        return win32process.GetExitCodeProcess(handle) == win32con.STILL_ACTIVE
    finally:
        win32api.CloseHandle(handle)


def _terminate_owned_fixture_process(pid: int) -> None:
    """Terminate only the surviving child PID written by this test fixture."""

    if not _windows_process_is_alive(pid):
        return
    import win32api
    import win32con
    import win32event
    import win32process

    handle = win32api.OpenProcess(
        win32con.PROCESS_TERMINATE | win32con.SYNCHRONIZE,
        False,
        pid,
    )
    try:
        win32process.TerminateProcess(handle, 0)
        win32event.WaitForSingleObject(handle, 5_000)
    finally:
        win32api.CloseHandle(handle)


async def _wait_for_fixture_process(pid: int, *, alive: bool) -> bool:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if _windows_process_is_alive(pid) is alive:
            return True
        await asyncio.sleep(0.05)
    return _windows_process_is_alive(pid) is alive


class _Win32Api:
    def __init__(self) -> None:
        self.closed: list[object] = []
        self.opened: list[tuple[int, int, bool, int]] = []

    def CloseHandle(self, handle: object) -> None:
        self.closed.append(handle)

    def OpenProcess(self, access: int, inherit: bool, pid: int) -> object:
        self.opened.append((access, 0, inherit, pid))
        return "process-handle"


class _Win32Job:
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x0800
    JobObjectExtendedLimitInformation = 9

    def __init__(self) -> None:
        self.created: list[tuple[object, str]] = []
        self.set_calls: list[tuple[object, int, dict[str, object]]] = []
        self.assignments: list[tuple[object, object]] = []

    def CreateJobObject(self, security: object, name: str) -> object:
        self.created.append((security, name))
        return "mcp-job"

    def QueryInformationJobObject(self, job: object, information_class: int) -> dict[str, object]:
        self.assert_job(job, information_class)
        return {"BasicLimitInformation": {"LimitFlags": 0x40}}

    def SetInformationJobObject(
        self, job: object, information_class: int, information: dict[str, object]
    ) -> None:
        self.set_calls.append((job, information_class, information))

    def AssignProcessToJobObject(self, job: object, process: object) -> None:
        self.assignments.append((job, process))

    def assert_job(self, job: object, information_class: int) -> None:
        if job != "mcp-job" or information_class != self.JobObjectExtendedLimitInformation:
            raise AssertionError("unexpected Job query")


class WindowsMcpTransportTests(unittest.TestCase):
    def test_gateway_uses_adapter_only_for_the_local_windows_mcp_child(self) -> None:
        with patch.object(mcp_client.os, "name", "nt"):
            transport = mcp_client._mcp_stdio_transport(
                command="controlstudio-plc-mcp",
                args=["--stdio"],
                environment={"CONTROLSTUDIO_MCP_AUTHORIZATION_SECRET": "test"},
                cwd=None,
            )
        self.assertIsInstance(transport, windows_mcp_transport.ControlStudioWindowsStdioTransport)

        with patch.object(mcp_client.os, "name", "posix"):
            fallback = mcp_client._mcp_stdio_transport(
                command="controlstudio-plc-mcp",
                args=[],
                environment={},
                cwd=None,
            )
        self.assertNotIsInstance(fallback, windows_mcp_transport.ControlStudioWindowsStdioTransport)

    def test_transport_job_keeps_cleanup_and_allows_only_explicit_breakaway(self) -> None:
        api = _Win32Api()
        job = _Win32Job()
        modules = {
            "win32api": api,
            "win32job": job,
            "win32con": types.SimpleNamespace(PROCESS_SET_QUOTA=0x100, PROCESS_TERMINATE=0x1),
        }
        with (
            patch.object(windows_mcp_transport.sys, "platform", "win32"),
            patch.dict(sys.modules, modules),
        ):
            created_job, created_api = (
                windows_mcp_transport._open_mcp_job_that_allows_explicit_gui_breakaway()
            )
            windows_mcp_transport._assign_mcp_process_to_cleanup_job(
                types.SimpleNamespace(pid=4242), created_job, created_api
            )

        self.assertEqual(created_job, "mcp-job")
        self.assertEqual(job.created, [(None, "")])
        self.assertEqual(len(job.set_calls), 1)
        flags = job.set_calls[0][2]["BasicLimitInformation"]["LimitFlags"]
        self.assertEqual(
            flags,
            0x40 | job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | job.JOB_OBJECT_LIMIT_BREAKAWAY_OK,
        )
        self.assertEqual(job.assignments, [("mcp-job", "process-handle")])
        self.assertEqual(api.opened, [(0x101, 0, False, 4242)])
        self.assertEqual(api.closed, ["process-handle"])


class WindowsMcpTransportProtocolSmokeTests(unittest.IsolatedAsyncioTestCase):
    def _transport(self, *args: str) -> windows_mcp_transport.ControlStudioWindowsStdioTransport:
        fixture = Path(__file__).parent / "fixtures" / "fake_mcp_stdio.py"
        return windows_mcp_transport.ControlStudioWindowsStdioTransport(
            command=sys.executable,
            args=[str(fixture), *args],
        )

    async def test_real_stdio_fixture_initializes_pings_lists_and_closes(self) -> None:
        client = Client(self._transport(), name="transport-smoke", init_timeout=5)
        async with client:
            self.assertEqual(client.initialize_result.serverInfo.name, "transport-fixture")
            self.assertTrue(await client.ping())
            self.assertEqual(await client.list_tools(), [])

    async def test_forced_cleanup_is_bounded_for_nonresponsive_fixture(self) -> None:
        client = Client(
            self._transport("--hold-after-initialize"), name="transport-forced-cleanup", init_timeout=5
        )
        started = time.monotonic()
        async with client:
            self.assertEqual(client.initialize_result.serverInfo.name, "transport-fixture")
        self.assertLess(time.monotonic() - started, 8.0)

    @unittest.skipUnless(sys.platform == "win32", "Windows Job behavior")
    async def test_transport_close_kills_ordinary_child_but_preserves_explicit_breakaway(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-mcp-job-") as temporary:
            pid_file = Path(temporary) / "fixture-children.json"
            client = Client(
                self._transport("--spawn-job-children", str(pid_file)),
                name="transport-job-breakaway",
                init_timeout=5,
            )
            ordinary_pid: int | None = None
            breakaway_pid: int | None = None
            try:
                async with client:
                    self.assertEqual(client.initialize_result.serverInfo.name, "transport-fixture")
                    self.assertTrue(pid_file.is_file(), "fixture reported child PIDs")
                    children = json.loads(pid_file.read_text(encoding="utf-8"))
                    ordinary_pid = int(children["ordinaryPid"])
                    breakaway_pid = int(children["breakawayPid"])
                    self.assertTrue(_windows_process_is_alive(ordinary_pid))
                    self.assertTrue(_windows_process_is_alive(breakaway_pid))

                assert ordinary_pid is not None
                assert breakaway_pid is not None
                self.assertTrue(await _wait_for_fixture_process(ordinary_pid, alive=False))
                self.assertTrue(await _wait_for_fixture_process(breakaway_pid, alive=True))
            finally:
                if breakaway_pid is not None:
                    with contextlib.suppress(Exception):
                        _terminate_owned_fixture_process(breakaway_pid)
                    self.assertTrue(await _wait_for_fixture_process(breakaway_pid, alive=False))


if __name__ == "__main__":
    unittest.main()
