"""Small inherited-privilege bootstrap used by :mod:`controlstudio_host.restart`."""

from __future__ import annotations

import argparse
import ctypes
import os
import socket
import sys
import time

_RESTART_LOG_FILE = None


def _wait_for_parent_exit(parent_pid: int, timeout_seconds: float | None = None) -> bool:
    """Wait on the exact Windows process handle; never terminate any process."""

    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (ctypes.c_uint, ctypes.c_int, ctypes.c_uint)
        open_process.restype = ctypes.c_void_p
        handle = open_process(0x00100000, False, parent_pid)  # SYNCHRONIZE
        if not handle:
            return True
        try:
            wait = kernel32.WaitForSingleObject
            wait.argtypes = (ctypes.c_void_p, ctypes.c_uint)
            wait.restype = ctypes.c_uint
            wait_ms = 0xFFFFFFFF if timeout_seconds is None else int(timeout_seconds * 1000)
            return wait(handle, wait_ms) == 0
        finally:
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (ctypes.c_void_p,)
            close_handle.restype = ctypes.c_int
            close_handle(handle)

    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    while deadline is None or time.monotonic() < deadline:
        try:
            os.kill(parent_pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        time.sleep(0.05)
    return False


def _port_is_bound(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(0.15)
        return client.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_port_release(port: int, timeout_seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _port_is_bound(port):
            return True
        time.sleep(0.05)
    return not _port_is_bound(port)


def _run_replacement_host() -> None:
    # Stay in the already elevated helper process.  A second detached Python
    # hop can lose the launch Job/environment boundary before it reports any
    # startup failure, leaving the browser with a scheduled receipt and no
    # replacement Host.
    # The helper is already the replacement process.  Its inherited launcher
    # Job name belongs to the original launch boundary and may no longer be
    # open after the old Host exits; rejoining it would prevent Uvicorn from
    # starting.  Fresh launcher starts still join from ``__main__`` unchanged.
    from .__main__ import main as host_main
    from .launch_job import LAUNCH_JOB_ENVIRONMENT_VARIABLE

    os.environ.pop(LAUNCH_JOB_ENVIRONMENT_VARIABLE, None)
    host_main()


def _redirect_replacement_output() -> None:
    """Retain replacement startup diagnostics even when the old Host used DEVNULL."""

    global _RESTART_LOG_FILE

    from .restart import _restart_log_path

    log_path = _restart_log_path(os.environ)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _RESTART_LOG_FILE = log_path.open("a", encoding="utf-8", buffering=1)
    sys.stdout = _RESTART_LOG_FILE
    sys.stderr = _RESTART_LOG_FILE


def restart_after_parent_exits(parent_pid: int, port: int) -> int:
    _redirect_replacement_output()
    print("CONTROLSTUDIO_HOST_RESTART waiting_for_parent_exit", file=sys.stderr, flush=True)
    if not _wait_for_parent_exit(parent_pid):
        print("CONTROLSTUDIO_HOST_RESTART parent_wait_failed", file=sys.stderr, flush=True)
        return 1
    if not _wait_for_port_release(port):
        print("CONTROLSTUDIO_HOST_RESTART port_release_failed", file=sys.stderr, flush=True)
        return 1
    _run_replacement_host()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--port", type=int, required=True)
    arguments = parser.parse_args()
    if arguments.parent_pid <= 0 or not 1 <= arguments.port <= 65535:
        return 2
    return restart_after_parent_exits(arguments.parent_pid, arguments.port)


if __name__ == "__main__":
    raise SystemExit(main())
