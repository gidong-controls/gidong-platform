from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import TracebackType
from typing import Protocol

from .constants import PORTABLE_LOCK_NAME, WINDOWS_MUTEX_NAME
from .errors import LauncherAlreadyRunning


class InstanceGuard(Protocol):
    def __enter__(self) -> InstanceGuard: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class WindowsNamedMutex:
    """Non-blocking, current-session named mutex.

    Windows releases ownership automatically if a launcher crashes, so no
    persistent startup marker or scheduled task is required.
    """

    _WAIT_OBJECT_0 = 0
    _WAIT_ABANDONED = 0x80

    def __init__(self, name: str = WINDOWS_MUTEX_NAME) -> None:
        self._name = name
        self._handle: int | None = None
        self._owned = False

    def __enter__(self) -> WindowsNamedMutex:
        if os.name != "nt":
            raise RuntimeError("WindowsNamedMutex is only available on Windows")

        import ctypes

        kernel32 = _load_kernel32()

        handle = kernel32.CreateMutexW(None, False, self._name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
        self._handle = int(handle)

        result = kernel32.WaitForSingleObject(handle, 0)
        if result not in {self._WAIT_OBJECT_0, self._WAIT_ABANDONED}:
            kernel32.CloseHandle(handle)
            self._handle = None
            raise LauncherAlreadyRunning("ControlStudio is already being launched")

        self._owned = True
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._handle is None:
            return

        kernel32 = _load_kernel32()
        if self._owned:
            kernel32.ReleaseMutex(self._handle)
        kernel32.CloseHandle(self._handle)
        self._owned = False
        self._handle = None


class PortableFileLock:
    """Non-Windows launcher guard backed by the host operating system."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or Path(tempfile.gettempdir()) / PORTABLE_LOCK_NAME
        self._file = None

    def __enter__(self) -> PortableFileLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._file.close()
            self._file = None
            raise LauncherAlreadyRunning("ControlStudio is already being launched") from exc
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None


def create_instance_guard() -> InstanceGuard:
    if os.name == "nt":
        return WindowsNamedMutex()
    return PortableFileLock()


def _load_kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32
