from __future__ import annotations

import os
import subprocess
import time
import webbrowser
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from .constants import CANONICAL_PORT
from .errors import UiOpenError

CONTROLSTUDIO_WINDOW_TITLE = "ControlStudio"
CONTROLSTUDIO_WINDOW_PROPERTY = "ControlStudio.LauncherOwned"
WINDOW_OPEN_TIMEOUT_SECONDS = 30.0
_BROWSER_EXECUTABLE_NAMES = frozenset({"chrome.exe", "msedge.exe"})


class UiOpener(Protocol):
    def focus_existing(self) -> bool: ...

    def open(self, url: str) -> None: ...

    def open_browser(self, url: str) -> None: ...


def build_app_mode_command(browser: Path, url: str) -> tuple[str, ...]:
    return (str(browser), f"--app={url}")


def _normalized_windows_path(path: Path | str) -> str:
    return str(path).replace("/", "\\").rstrip("\\").casefold()


def _is_allowed_browser_process_path(
    process_path: Path,
    browser_paths: tuple[Path, ...],
) -> bool:
    normalized_process = _normalized_windows_path(process_path)
    return process_path.name.casefold() in _BROWSER_EXECUTABLE_NAMES and normalized_process in {
        _normalized_windows_path(browser_path) for browser_path in browser_paths
    }


def _window_process_path(window: int) -> Path:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = (
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    )
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = (
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
    if not process_id.value:
        raise ctypes.WinError(ctypes.get_last_error())

    process = kernel32.OpenProcess(
        0x1000,  # PROCESS_QUERY_LIMITED_INFORMATION
        False,
        process_id.value,
    )
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        capacity = wintypes.DWORD(32768)
        executable = ctypes.create_unicode_buffer(capacity.value)
        if not kernel32.QueryFullProcessImageNameW(
            process,
            0,
            executable,
            ctypes.byref(capacity),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return Path(executable.value)
    finally:
        kernel32.CloseHandle(process)


def _controlstudio_windows(
    browser_paths: tuple[Path, ...] | None = None,
    *,
    require_launcher_marker: bool = False,
) -> tuple[int, ...]:
    if os.name != "nt":
        return ()

    import ctypes
    from ctypes import wintypes

    allowed_browsers = browser_paths or discover_windows_app_browsers()
    if not allowed_browsers:
        return ()

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    matching_window: list[int] = []
    ambiguous_window: list[int] = []
    enum_callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = (
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    )
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = (
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    )
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetPropW.argtypes = (wintypes.HWND, wintypes.LPCWSTR)
    user32.GetPropW.restype = wintypes.HANDLE
    user32.EnumWindows.argtypes = (enum_callback, wintypes.LPARAM)
    user32.EnumWindows.restype = wintypes.BOOL

    @enum_callback
    def inspect_window(window: int, _context: int) -> bool:
        if not user32.IsWindowVisible(window):
            return True

        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(window, class_name, len(class_name))
        if not class_name.value.startswith("Chrome_WidgetWin_"):
            return True
        if require_launcher_marker:
            if not user32.GetPropW(window, CONTROLSTUDIO_WINDOW_PROPERTY):
                return True
        else:
            title_length = user32.GetWindowTextLengthW(window)
            if title_length <= 0:
                return True
            title = ctypes.create_unicode_buffer(title_length + 1)
            user32.GetWindowTextW(window, title, len(title))
            if title.value.strip() != CONTROLSTUDIO_WINDOW_TITLE:
                return True

        try:
            process_path = _window_process_path(int(window))
        except OSError:
            ambiguous_window.append(int(window))
            return True
        if _is_allowed_browser_process_path(process_path, allowed_browsers):
            matching_window.append(int(window))
        else:
            ambiguous_window.append(int(window))
        return True

    user32.EnumWindows(inspect_window, 0)
    if ambiguous_window:
        raise OSError("a ControlStudio-titled Chromium window has an unverified owner process")
    return tuple(matching_window)


def _launcher_owned_controlstudio_windows(
    browser_paths: tuple[Path, ...],
) -> tuple[int, ...]:
    candidates = _controlstudio_windows(browser_paths)
    launcher_owned = _controlstudio_windows(
        browser_paths,
        require_launcher_marker=True,
    )
    unmarked = tuple(sorted(set(candidates) - set(launcher_owned)))
    for window in unmarked:
        # Chromium can recreate its top-level HWND after a renderer crash or a
        # delayed first launch, which loses the transient Win32 property set by
        # the Launcher. Recover ownership only when the allowed browser process
        # also exposes the exact loopback ControlStudio origin through UIA.
        if not _window_shows_controlstudio_origin(window):
            raise OSError("a matching ControlStudio window is not marked as launcher-owned")
        if not _set_launcher_owned_marker(window):
            raise OSError("the ControlStudio window ownership marker could not be restored")

    if unmarked:
        launcher_owned = _controlstudio_windows(
            browser_paths,
            require_launcher_marker=True,
        )
        if set(candidates) - set(launcher_owned):
            raise OSError("a matching ControlStudio window is not marked as launcher-owned")
    return launcher_owned


def _window_shows_controlstudio_origin(window: int) -> bool:
    try:
        from pywinauto import Desktop

        app_window = Desktop(backend="uia").window(handle=window)
        values = [
            str(control.window_text() or "").strip()
            for control in app_window.descendants(control_type="Edit")
        ]
    except Exception:
        return False

    for value in values:
        if not value:
            continue
        candidate = value if "://" in value else f"http://{value}"
        try:
            parsed = urlsplit(candidate)
            port = parsed.port
        except ValueError:
            continue
        if (
            parsed.scheme == "http"
            and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            and port == CANONICAL_PORT
            and parsed.path in {"", "/"}
            and not parsed.username
            and not parsed.password
            and not parsed.query
        ):
            return True
    return False


def _set_launcher_owned_marker(window: int) -> bool:
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetPropW.argtypes = (
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.HANDLE,
    )
    user32.SetPropW.restype = wintypes.BOOL
    return bool(user32.SetPropW(window, CONTROLSTUDIO_WINDOW_PROPERTY, 1))


def close_existing_controlstudio_windows(
    browser_paths: tuple[Path, ...],
    *,
    timeout_seconds: float = 2.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    post_close: Callable[[int], bool] | None = None,
    window_exists: Callable[[int], bool] | None = None,
) -> bool:
    """Close every verified ControlStudio browser window before a fresh handoff."""
    matching_windows = _launcher_owned_controlstudio_windows(browser_paths)
    if not matching_windows:
        return True

    if post_close is None or window_exists is None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostMessageW.argtypes = (
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        user32.PostMessageW.restype = wintypes.BOOL
        user32.IsWindow.argtypes = (wintypes.HWND,)
        user32.IsWindow.restype = wintypes.BOOL
        if post_close is None:

            def post_close(window: int) -> bool:
                return bool(user32.PostMessageW(window, 0x0010, 0, 0))

        if window_exists is None:

            def window_exists(window: int) -> bool:
                return bool(user32.IsWindow(window))

    for window in matching_windows:
        if not post_close(window):
            return False

    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        if not any(window_exists(window) for window in matching_windows):
            # Chromium drops transient properties while closing. Do not
            # re-authenticate a dying HWND; only ensure no exact allowed
            # ControlStudio window was recreated behind the launcher's back.
            return not _controlstudio_windows(browser_paths)
        sleep(0.05)
    return not any(window_exists(window) for window in matching_windows)


def _focus_controlstudio_window(
    window: int,
    *,
    user32: object | None = None,
    kernel32: object | None = None,
) -> bool:
    if user32 is None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.ShowWindow.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.FlashWindow.argtypes = (wintypes.HWND, wintypes.BOOL)
        user32.FlashWindow.restype = wintypes.BOOL
        user32.GetForegroundWindow.argtypes = ()
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.SwitchToThisWindow.argtypes = (wintypes.HWND, wintypes.BOOL)
        user32.SwitchToThisWindow.restype = None
        user32.GetWindowThreadProcessId.argtypes = (
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        )
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.AttachThreadInput.argtypes = (
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.BOOL,
        )
        user32.AttachThreadInput.restype = wintypes.BOOL
        user32.BringWindowToTop.argtypes = (wintypes.HWND,)
        user32.BringWindowToTop.restype = wintypes.BOOL
        user32.SetActiveWindow.argtypes = (wintypes.HWND,)
        user32.SetActiveWindow.restype = wintypes.HWND
        kernel32.GetCurrentThreadId.argtypes = ()
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    user32.ShowWindow(window, 9)  # SW_RESTORE; return value is prior visibility.
    if user32.SetForegroundWindow(window):
        return True
    switch_to_window = getattr(user32, "SwitchToThisWindow", None)
    get_foreground_window = getattr(user32, "GetForegroundWindow", None)
    if callable(switch_to_window) and callable(get_foreground_window):
        try:
            switch_to_window(window, True)
            if int(get_foreground_window() or 0) == int(window):
                return True
        except Exception:
            pass
    get_window_thread = getattr(user32, "GetWindowThreadProcessId", None)
    attach_thread_input = getattr(user32, "AttachThreadInput", None)
    bring_to_top = getattr(user32, "BringWindowToTop", None)
    set_active_window = getattr(user32, "SetActiveWindow", None)
    get_current_thread = getattr(kernel32, "GetCurrentThreadId", None)
    if all(
        callable(value)
        for value in (
            get_window_thread,
            attach_thread_input,
            bring_to_top,
            set_active_window,
            get_foreground_window,
            get_current_thread,
        )
    ):
        current_thread = int(get_current_thread())
        target_thread = int(get_window_thread(window, None))
        foreground = int(get_foreground_window() or 0)
        foreground_thread = int(get_window_thread(foreground, None)) if foreground else 0
        attached: list[int] = []
        try:
            for thread_id in dict.fromkeys((target_thread, foreground_thread)):
                if (
                    thread_id
                    and thread_id != current_thread
                    and attach_thread_input(current_thread, thread_id, True)
                ):
                    attached.append(thread_id)
            bring_to_top(window)
            set_active_window(window)
            if user32.SetForegroundWindow(window):
                return True
            if int(get_foreground_window() or 0) == int(window):
                return True
        except Exception:
            pass
        finally:
            for thread_id in reversed(attached):
                with suppress(Exception):
                    attach_thread_input(current_thread, thread_id, False)
    user32.FlashWindow(window, True)
    return False


def wait_for_single_controlstudio_window(
    browser: Path,
    *,
    timeout_seconds: float = WINDOW_OPEN_TIMEOUT_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Wait until exactly one verified browser app window exists.

    UIA cannot reliably expose Edge app-mode documents on every supported
    Windows/Edge combination. Browser bootstrap readiness is instead proven by
    the Host's one-time protocol receipt in ``ControlStudioLauncher``. This
    routine deliberately performs only process/ownership checks.
    """

    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        matching_windows = _controlstudio_windows((browser,))
        if matching_windows:
            if len(matching_windows) != 1:
                # Chromium app-mode can expose transient top-level windows.
                sleep(0.05)
                continue
            window = matching_windows[0]
            if not _set_launcher_owned_marker(window):
                return False
            if (
                _controlstudio_windows(
                    (browser,),
                    require_launcher_marker=True,
                )
                != matching_windows
            ):
                return False
            # The launcher verifies protocol bootstrap separately. Do not infer
            # it from browser title, pixels, or accessibility tree state.
            _focus_controlstudio_window(window)
            return True
        sleep(0.05)
    return False


def discover_windows_app_browsers(
    environ: Mapping[str, str] | None = None,
    *,
    exists: Callable[[Path], bool] = Path.is_file,
) -> tuple[Path, ...]:
    values = os.environ if environ is None else environ
    roots = [
        values.get("PROGRAMFILES(X86)"),
        values.get("PROGRAMFILES"),
        values.get("LOCALAPPDATA"),
    ]
    relative_paths = (
        Path("Microsoft/Edge/Application/msedge.exe"),
        Path("Google/Chrome/Application/chrome.exe"),
    )

    candidates: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root:
            continue
        for relative in relative_paths:
            candidate = Path(root) / relative
            normalized = str(candidate).casefold()
            if normalized not in seen and exists(candidate):
                candidates.append(candidate)
                seen.add(normalized)
    return tuple(candidates)


class SystemUiOpener:
    def __init__(
        self,
        *,
        platform_name: str = os.name,
        popen: Callable[..., object] = subprocess.Popen,
        browser_open: Callable[..., bool] = webbrowser.open,
        existing_window_closer: Callable[
            [tuple[Path, ...]], bool
        ] = close_existing_controlstudio_windows,
        opened_window_waiter: Callable[[Path], bool] = wait_for_single_controlstudio_window,
    ) -> None:
        self._platform_name = platform_name
        self._popen = popen
        self._browser_open = browser_open
        self._existing_window_closer = existing_window_closer
        self._opened_window_waiter = opened_window_waiter

    def focus_existing(self) -> bool:
        """Focus one verified launcher-owned window without replacing its UI state.

        Multiple verified windows are a corrupted legacy state. Close all of them
        under the launcher mutex so the caller can create one fresh authenticated
        window. An unverified lookalike is never focused or closed.
        """

        if self._platform_name != "nt":
            return False

        browsers = discover_windows_app_browsers()
        if not browsers:
            return False
        try:
            matching_windows = _launcher_owned_controlstudio_windows(browsers)
        except OSError as exc:
            raise UiOpenError("could not verify the existing ControlStudio window safely") from exc

        if not matching_windows:
            return False
        if len(matching_windows) > 1:
            try:
                closed = self._existing_window_closer(browsers)
            except OSError as exc:
                raise UiOpenError(
                    "could not repair multiple existing ControlStudio windows safely"
                ) from exc
            if not closed:
                raise UiOpenError("could not repair multiple existing ControlStudio windows safely")
            return False

        if not _focus_controlstudio_window(matching_windows[0]):
            raise UiOpenError(
                "the existing ControlStudio window was restored but Windows "
                "would not bring it to the foreground"
            )
        return True

    def open(self, url: str) -> None:
        if self._platform_name == "nt":
            browsers = discover_windows_app_browsers()
            if not browsers:
                raise UiOpenError(
                    "could not find Microsoft Edge or Google Chrome to open ControlStudio"
                )
            try:
                if not self._existing_window_closer(browsers):
                    raise UiOpenError("could not replace the existing ControlStudio window safely")
            except OSError as exc:
                raise UiOpenError(
                    "could not verify and replace the existing ControlStudio window safely"
                ) from exc

            for browser in browsers:
                try:
                    self._popen(
                        build_app_mode_command(browser, url),
                        shell=False,
                        close_fds=True,
                    )
                except OSError:
                    continue
                try:
                    window_ready = self._opened_window_waiter(browser)
                except OSError as exc:
                    window_ready = False
                    window_error = exc
                else:
                    window_error = None
                if window_ready:
                    return
                with suppress(OSError):
                    self._existing_window_closer(browsers)
                if window_error is not None:
                    raise UiOpenError(
                        "ControlStudio opened but its app window ownership could not be verified"
                    ) from window_error
                raise UiOpenError("ControlStudio did not produce exactly one foreground app window")
            raise UiOpenError("could not start Microsoft Edge or Google Chrome")

        if not self._browser_open(url, new=1, autoraise=True):
            raise UiOpenError(f"could not open ControlStudio UI; open {url} manually")

    def open_browser(self, url: str) -> None:
        """Open a normal browser tab without inspecting or reusing PWA windows."""

        if not self._browser_open(url, new=1, autoraise=True):
            raise UiOpenError(f"could not open ControlStudio UI; open {url} manually")
