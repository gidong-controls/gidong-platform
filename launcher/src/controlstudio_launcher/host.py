from __future__ import annotations

import ctypes
import os
import subprocess
from collections.abc import Mapping
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .constants import (
    CANONICAL_BASE_URL,
    CANONICAL_BIND_HOST,
    CANONICAL_PORT,
    PRODUCT_DATA_DIRECTORY_NAME,
)

LAUNCH_JOB_ENVIRONMENT_VARIABLE = "CONTROLSTUDIO_LAUNCH_JOB_NAME"
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800


class HostProcess(Protocol):
    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


class WindowsJobHostProcess:
    """A spawned Host root plus the Job used only for failure cleanup.

    The Job deliberately has no kill-on-close limit. A successful launcher
    releases its handle and exits while the Host keeps running. Failure cleanup
    explicitly terminates the Job, which includes descendants such as the base
    Python interpreter and MCP process.
    """

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        job_handle: int,
    ) -> None:
        self._process = process
        self._job_handle: int | None = job_handle

    def poll(self) -> int | None:
        job_handle = self._job_handle
        if job_handle is not None:
            try:
                if _windows_job_has_active_processes(job_handle):
                    return None
            except OSError:
                # Fall back to the root process result. The launcher will fail
                # closed if that root has exited and readiness was not proven.
                pass
        root_result = self._process.poll()
        if job_handle is not None and root_result == 0:
            # A successful venv redirector can exit just before the real
            # interpreter joins the named Job. Keep probing readiness; a
            # command that produced no Host will hit the normal startup timeout.
            return None
        return root_result

    def terminate(self) -> None:
        job_handle = self._job_handle
        if job_handle is not None:
            _terminate_windows_job(job_handle)
        elif self._process.poll() is None:
            self._process.terminate()

    def wait(self, timeout: float | None = None) -> int:
        return self._process.wait(timeout=timeout)

    def release(self) -> None:
        job_handle = self._job_handle
        self._job_handle = None
        if job_handle is not None:
            _close_windows_handle(job_handle)


class HostStarter(Protocol):
    def start(
        self,
        command: tuple[str, ...],
        cwd: Path | None,
        environment: Mapping[str, str],
    ) -> HostProcess: ...


class SubprocessHostStarter:
    """Start exactly the locally configured argv without a command shell."""

    def start(
        self,
        command: tuple[str, ...],
        cwd: Path | None,
        environment: Mapping[str, str],
    ) -> HostProcess:
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        child_environment = os.environ.copy()
        child_environment.update(environment)
        # The first product increment has one canonical loopback identity. Do
        # not let inherited developer or machine environment variables start
        # the child on an endpoint different from the one the launcher proves
        # and opens.
        child_environment.update(
            {
                "CONTROLSTUDIO_BIND_HOST": CANONICAL_BIND_HOST,
                "CONTROLSTUDIO_PORT": str(CANONICAL_PORT),
                "CONTROLSTUDIO_CANONICAL_ORIGIN": CANONICAL_BASE_URL,
            }
        )
        if os.name != "nt":
            return subprocess.Popen(
                command,
                cwd=cwd,
                env=child_environment,
                shell=False,
                close_fds=True,
                creationflags=creation_flags,
            )

        child_environment["PYTHONIOENCODING"] = "utf-8"
        job_handle, job_name = _create_windows_job()
        try:
            _allow_verified_ide_breakaway(job_handle)
        except BaseException:
            _close_windows_handle(job_handle)
            raise
        child_environment[LAUNCH_JOB_ENVIRONMENT_VARIABLE] = job_name
        startup_log = _open_startup_log()
        integrity = _current_integrity_for_log()
        _write_startup_log(
            startup_log,
            f"launcher_pid={os.getpid()} integrity={integrity} "
            f"job={job_name!r} executable={command[0]!r} "
            f"argument_count={len(command) - 1}",
        )
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=child_environment,
                shell=False,
                close_fds=True,
                creationflags=creation_flags,
                stdout=startup_log,
                stderr=subprocess.STDOUT,
            )
            _write_startup_log(startup_log, f"spawned_pid={process.pid}")
        except BaseException:
            _close_windows_handle(job_handle)
            startup_log.close()
            raise
        try:
            process_handle = int(process._handle)
            _assign_process_to_windows_job(job_handle, process_handle)
            _write_startup_log(
                startup_log,
                f"assigned_pid={process.pid} process_handle={process_handle}",
            )
        except BaseException:
            # Assignment is the only guarantee that failure cleanup cannot
            # orphan the Host's base interpreter or MCP descendants.
            process.terminate()
            with suppress(TimeoutError, subprocess.TimeoutExpired):
                process.wait(timeout=5)
            _close_windows_handle(job_handle)
            raise
        finally:
            startup_log.close()
        return WindowsJobHostProcess(process, job_handle)


def stop_process_started_by_launcher(process: HostProcess) -> None:
    """Best-effort cleanup of only the process this launcher started."""

    try:
        process_running = process.poll() is None
        if process_running or isinstance(process, WindowsJobHostProcess):
            process.terminate()
        if process_running:
            # The Windows Job termination request has already targeted the
            # exact launcher-owned tree. Never escalate to an unrelated PID.
            with suppress(TimeoutError, subprocess.TimeoutExpired):
                process.wait(timeout=5)
    finally:
        release_process_started_by_launcher(process)


def release_process_started_by_launcher(process: HostProcess) -> None:
    """Release optional launcher-only resources without stopping the Host."""

    release = getattr(process, "release", None)
    if callable(release):
        release()


def _create_windows_job() -> tuple[int, str]:
    job_name = rf"Local\ControlStudio.Launcher.{uuid4()}"
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_job = kernel32.CreateJobObjectW
    create_job.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
    create_job.restype = ctypes.c_void_p
    ctypes.set_last_error(0)
    handle = create_job(None, job_name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        _close_windows_handle(int(handle))
        raise FileExistsError("generated ControlStudio launch Job name already exists")
    return int(handle), job_name


def _allow_verified_ide_breakaway(job_handle: int) -> None:
    """Permit the trusted XG5000 launcher to leave the Host cleanup Job.

    The Host/MCP tree remains in this Job for startup-failure cleanup.  Once
    the MCP has proved an XG5000 working-copy binding, that visible IDE is a
    separately managed engineering session: reloads must preserve its exact
    PID/HWND/create-time identity and never close or recreate it implicitly.
    ``project_launcher`` is the sole product path that asks for breakaway.
    """

    class _LargeInteger(ctypes.Structure):
        _fields_ = (("quad_part", ctypes.c_longlong),)

    class _IoCounters(ctypes.Structure):
        _fields_ = (
            ("read_operation_count", ctypes.c_ulonglong),
            ("write_operation_count", ctypes.c_ulonglong),
            ("other_operation_count", ctypes.c_ulonglong),
            ("read_transfer_count", ctypes.c_ulonglong),
            ("write_transfer_count", ctypes.c_ulonglong),
            ("other_transfer_count", ctypes.c_ulonglong),
        )

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = (
            ("per_process_user_time_limit", _LargeInteger),
            ("per_job_user_time_limit", _LargeInteger),
            ("limit_flags", ctypes.c_uint32),
            ("minimum_working_set_size", ctypes.c_size_t),
            ("maximum_working_set_size", ctypes.c_size_t),
            ("active_process_limit", ctypes.c_uint32),
            ("affinity", ctypes.c_size_t),
            ("priority_class", ctypes.c_uint32),
            ("scheduling_class", ctypes.c_uint32),
        )

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = (
            ("basic_limit_information", _BasicLimitInformation),
            ("io_info", _IoCounters),
            ("process_memory_limit", ctypes.c_size_t),
            ("job_memory_limit", ctypes.c_size_t),
            ("peak_process_memory_used", ctypes.c_size_t),
            ("peak_job_memory_used", ctypes.c_size_t),
        )

    information = _ExtendedLimitInformation()
    information.basic_limit_information.limit_flags = _JOB_OBJECT_LIMIT_BREAKAWAY_OK
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_information = kernel32.SetInformationJobObject
    set_information.argtypes = (
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    )
    set_information.restype = ctypes.c_int
    if not set_information(
        ctypes.c_void_p(job_handle),
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        raise ctypes.WinError(ctypes.get_last_error())


def _open_startup_log():
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        log_directory = Path(local_app_data) / PRODUCT_DATA_DIRECTORY_NAME / "logs"
        try:
            log_directory.mkdir(parents=True, exist_ok=True)
            return (log_directory / "host-startup.log").open("ab")
        except OSError:
            pass
    return open(os.devnull, "ab")


def _write_startup_log(stream, message: str) -> None:
    try:
        timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
        stream.write((f"{timestamp} {message}\r\n").encode("utf-8", errors="replace"))
        stream.flush()
    except (OSError, ValueError):
        pass


def write_launcher_diagnostic(message: str) -> None:
    stream = _open_startup_log()
    try:
        _write_startup_log(stream, message)
    finally:
        stream.close()


def _current_integrity_for_log() -> str:
    try:
        from .integrity import _process_integrity_level

        return f"0x{_process_integrity_level(os.getpid()):x}"
    except OSError:
        return "unknown"


def _assign_process_to_windows_job(job_handle: int, process_handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    assign = kernel32.AssignProcessToJobObject
    assign.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    assign.restype = ctypes.c_int
    if not assign(job_handle, process_handle):
        raise ctypes.WinError(ctypes.get_last_error())


def _terminate_windows_job(job_handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    terminate = kernel32.TerminateJobObject
    terminate.argtypes = (ctypes.c_void_p, ctypes.c_uint)
    terminate.restype = ctypes.c_int
    if not terminate(job_handle, 1):
        raise ctypes.WinError(ctypes.get_last_error())


def _windows_job_has_active_processes(job_handle: int) -> bool:
    """Return whether the launch Job still owns a live Host process.

    A virtual-environment ``python.exe`` can be a short-lived redirector. Its
    process may exit successfully while the real base interpreter continues in
    the inherited Job. Readiness must follow the Job tree, not just that
    redirector PID.
    """

    class _JobObjectBasicAccountingInformation(ctypes.Structure):
        _fields_ = (
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", ctypes.c_uint32),
            ("TotalProcesses", ctypes.c_uint32),
            ("ActiveProcesses", ctypes.c_uint32),
            ("TotalTerminatedProcesses", ctypes.c_uint32),
        )

    information = _JobObjectBasicAccountingInformation()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    query = kernel32.QueryInformationJobObject
    query.argtypes = (
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    )
    query.restype = ctypes.c_int
    returned_length = ctypes.c_uint32()
    if not query(
        job_handle,
        1,  # JobObjectBasicAccountingInformation
        ctypes.byref(information),
        ctypes.sizeof(information),
        ctypes.byref(returned_length),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return information.ActiveProcesses > 0


def _close_windows_handle(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close = kernel32.CloseHandle
    close.argtypes = (ctypes.c_void_p,)
    close.restype = ctypes.c_int
    if not close(handle):
        raise ctypes.WinError(ctypes.get_last_error())
