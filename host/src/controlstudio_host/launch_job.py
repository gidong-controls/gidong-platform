from __future__ import annotations

import ctypes
import os
from collections.abc import Mapping

LAUNCH_JOB_ENVIRONMENT_VARIABLE = "CONTROLSTUDIO_LAUNCH_JOB_NAME"

_JOB_OBJECT_ASSIGN_PROCESS = 0x0001
_JOB_OBJECT_QUERY = 0x0004


def join_launcher_job_from_environment(
    environment: Mapping[str, str] | None = None,
) -> None:
    """Join the exact launcher-owned Windows Job before starting Host children.

    Windows virtual-environment executables may redirect to a second CPython
    process before the launcher can assign the redirector to its Job. The
    launcher therefore passes a unique named Job. The real Host process joins
    it here before Uvicorn or MCP is started, closing that process-tree race.
    """

    values = os.environ if environment is None else environment
    job_name = values.get(LAUNCH_JOB_ENVIRONMENT_VARIABLE)
    if not job_name:
        return
    if os.name != "nt":
        raise RuntimeError("a ControlStudio launch Job was requested outside Windows")
    _join_windows_job(job_name)


def _join_windows_job(job_name: str) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    open_job = kernel32.OpenJobObjectW
    open_job.argtypes = (ctypes.c_uint, ctypes.c_int, ctypes.c_wchar_p)
    open_job.restype = ctypes.c_void_p
    job_handle = open_job(
        _JOB_OBJECT_ASSIGN_PROCESS | _JOB_OBJECT_QUERY,
        False,
        job_name,
    )
    if not job_handle:
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        get_current_process = kernel32.GetCurrentProcess
        get_current_process.argtypes = ()
        get_current_process.restype = ctypes.c_void_p
        process_handle = get_current_process()

        is_process_in_job = kernel32.IsProcessInJob
        is_process_in_job.argtypes = (
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
        )
        is_process_in_job.restype = ctypes.c_int
        is_member = ctypes.c_int()
        if not is_process_in_job(
            process_handle,
            job_handle,
            ctypes.byref(is_member),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if is_member.value:
            return

        assign = kernel32.AssignProcessToJobObject
        assign.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        assign.restype = ctypes.c_int
        if not assign(job_handle, process_handle):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (ctypes.c_void_p,)
        close_handle.restype = ctypes.c_int
        close_handle(job_handle)
