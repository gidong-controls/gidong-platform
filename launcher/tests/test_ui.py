from __future__ import annotations

from pathlib import Path

import pytest

from controlstudio_launcher.errors import UiOpenError
from controlstudio_launcher.ui import (
    SystemUiOpener,
    _focus_controlstudio_window,
    _is_allowed_browser_process_path,
    _launcher_owned_controlstudio_windows,
    _refresh_controlstudio_window,
    build_app_mode_command,
    close_existing_controlstudio_windows,
    discover_windows_app_browsers,
    wait_for_single_controlstudio_window,
)


def test_app_mode_command_is_argv_not_shell_text() -> None:
    command = build_app_mode_command(
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
        "http://localhost:8765",
    )

    assert command == (
        "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
        "--app=http://localhost:8765",
    )


def test_windows_browser_discovery_prefers_edge_then_chrome_per_root() -> None:
    existing = {
        "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe".casefold(),
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe".casefold(),
    }

    browsers = discover_windows_app_browsers(
        {"PROGRAMFILES": "C:/Program Files"},
        exists=lambda path: str(path).casefold() in existing,
    )

    assert [path.name for path in browsers] == ["msedge.exe", "chrome.exe"]


def test_default_browser_is_used_when_not_on_windows() -> None:
    calls: list[tuple[str, int, bool]] = []
    opener = SystemUiOpener(
        platform_name="posix",
        browser_open=lambda url, new, autoraise: calls.append((url, new, autoraise)) or True,
    )

    opener.open("http://localhost:8765")

    assert calls == [("http://localhost:8765", 1, True)]


def test_existing_verified_window_is_focused_without_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser = Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe")
    close_calls: list[tuple[Path, ...]] = []
    monkeypatch.setattr(
        "controlstudio_launcher.ui.discover_windows_app_browsers",
        lambda: (browser,),
    )
    monkeypatch.setattr(
        "controlstudio_launcher.ui._launcher_owned_controlstudio_windows",
        lambda _browsers: (42,),
    )
    monkeypatch.setattr(
        "controlstudio_launcher.ui._focus_controlstudio_window",
        lambda window: window == 42,
    )
    opener = SystemUiOpener(
        platform_name="nt",
        existing_window_closer=lambda browsers: close_calls.append(browsers) or True,
    )

    assert opener.focus_existing()
    assert close_calls == []


def test_multiple_verified_windows_are_closed_before_fresh_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser = Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe")
    close_calls: list[tuple[Path, ...]] = []
    monkeypatch.setattr(
        "controlstudio_launcher.ui.discover_windows_app_browsers",
        lambda: (browser,),
    )
    monkeypatch.setattr(
        "controlstudio_launcher.ui._launcher_owned_controlstudio_windows",
        lambda _browsers: (42, 84),
    )
    opener = SystemUiOpener(
        platform_name="nt",
        existing_window_closer=lambda browsers: close_calls.append(browsers) or True,
    )

    assert not opener.focus_existing()
    assert close_calls == [(browser,)]


def test_existing_window_foreground_failure_preserves_its_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser = Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe")
    close_calls: list[tuple[Path, ...]] = []
    monkeypatch.setattr(
        "controlstudio_launcher.ui.discover_windows_app_browsers",
        lambda: (browser,),
    )
    monkeypatch.setattr(
        "controlstudio_launcher.ui._launcher_owned_controlstudio_windows",
        lambda _browsers: (42,),
    )
    monkeypatch.setattr(
        "controlstudio_launcher.ui._focus_controlstudio_window",
        lambda _window: False,
    )
    opener = SystemUiOpener(
        platform_name="nt",
        existing_window_closer=lambda browsers: close_calls.append(browsers) or True,
    )

    with pytest.raises(UiOpenError, match="would not bring it to the foreground"):
        opener.focus_existing()

    assert close_calls == []


def test_multiple_window_repair_fails_closed_when_close_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser = Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe")
    monkeypatch.setattr(
        "controlstudio_launcher.ui.discover_windows_app_browsers",
        lambda: (browser,),
    )
    monkeypatch.setattr(
        "controlstudio_launcher.ui._launcher_owned_controlstudio_windows",
        lambda _browsers: (42, 84),
    )
    opener = SystemUiOpener(
        platform_name="nt",
        existing_window_closer=lambda _browsers: False,
    )

    with pytest.raises(UiOpenError, match="could not repair multiple"):
        opener.focus_existing()


def test_unverified_existing_window_fails_closed_without_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser = Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe")
    close_calls: list[tuple[Path, ...]] = []
    monkeypatch.setattr(
        "controlstudio_launcher.ui.discover_windows_app_browsers",
        lambda: (browser,),
    )

    def reject(_browsers: tuple[Path, ...]) -> tuple[int, ...]:
        raise OSError("unverified lookalike")

    monkeypatch.setattr(
        "controlstudio_launcher.ui._launcher_owned_controlstudio_windows",
        reject,
    )
    opener = SystemUiOpener(
        platform_name="nt",
        existing_window_closer=lambda browsers: close_calls.append(browsers) or True,
    )

    with pytest.raises(UiOpenError, match="could not verify"):
        opener.focus_existing()

    assert close_calls == []


def test_every_launch_replaces_existing_window_and_waits_for_new_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    popen_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    browser_calls: list[tuple[str, int, bool]] = []
    browser = Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe")
    monkeypatch.setattr(
        "controlstudio_launcher.ui.discover_windows_app_browsers",
        lambda: (browser,),
    )
    opener = SystemUiOpener(
        platform_name="nt",
        popen=lambda *args, **kwargs: (
            events.append("popen"),
            popen_calls.append((args, kwargs)),
        ),
        browser_open=lambda url, new, autoraise: browser_calls.append(
            (url, new, autoraise)
        )
        or True,
        existing_window_closer=lambda browsers: events.append(
            f"close:{len(browsers)}"
        )
        or True,
        opened_window_waiter=lambda selected: events.append(
            f"wait:{selected.name}"
        )
        or True,
    )

    opener.open("http://localhost:8765/#handoff_ticket=fresh")

    assert events == ["close:1", "popen", "wait:msedge.exe"]
    assert len(popen_calls) == 1
    assert popen_calls[0][0][0][1] == (
        "--app=http://localhost:8765/#handoff_ticket=fresh"
    )
    assert browser_calls == []


def test_second_launch_replaces_first_with_its_fresh_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser = Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe")
    commands: list[tuple[str, ...]] = []
    close_calls = 0
    wait_calls = 0

    def close(_browsers: tuple[Path, ...]) -> bool:
        nonlocal close_calls
        close_calls += 1
        return True

    def wait(_browser: Path) -> bool:
        nonlocal wait_calls
        wait_calls += 1
        return True

    monkeypatch.setattr(
        "controlstudio_launcher.ui.discover_windows_app_browsers",
        lambda: (browser,),
    )
    opener = SystemUiOpener(
        platform_name="nt",
        popen=lambda command, **_kwargs: commands.append(command),
        existing_window_closer=close,
        opened_window_waiter=wait,
    )

    opener.open("http://localhost:8765/#handoff_ticket=first")
    opener.open("http://localhost:8765/#handoff_ticket=second")

    assert close_calls == 2
    assert wait_calls == 2
    assert [command[1] for command in commands] == [
        "--app=http://localhost:8765/#handoff_ticket=first",
        "--app=http://localhost:8765/#handoff_ticket=second",
    ]


def test_launch_fails_closed_when_existing_window_cannot_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "controlstudio_launcher.ui.discover_windows_app_browsers",
        lambda: (Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),),
    )
    opener = SystemUiOpener(
        platform_name="nt",
        existing_window_closer=lambda _browsers: False,
    )

    with pytest.raises(
        UiOpenError,
        match="could not replace the existing ControlStudio window safely",
    ):
        opener.open("http://localhost:8765")


def test_launch_closes_unverified_new_window_when_wait_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser = Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe")
    close_calls: list[tuple[Path, ...]] = []
    monkeypatch.setattr(
        "controlstudio_launcher.ui.discover_windows_app_browsers",
        lambda: (browser,),
    )
    opener = SystemUiOpener(
        platform_name="nt",
        popen=lambda *_args, **_kwargs: object(),
        existing_window_closer=lambda browsers: close_calls.append(browsers) or True,
        opened_window_waiter=lambda _browser: False,
    )

    with pytest.raises(
        UiOpenError,
        match="did not produce exactly one foreground app window",
    ):
        opener.open("http://localhost:8765")

    assert close_calls == [(browser,), (browser,)]


def test_windows_launch_does_not_fallback_to_unmanaged_browser_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser_calls: list[str] = []
    monkeypatch.setattr(
        "controlstudio_launcher.ui.discover_windows_app_browsers",
        lambda: (),
    )
    opener = SystemUiOpener(
        platform_name="nt",
        browser_open=lambda url, **_kwargs: browser_calls.append(url) or True,
    )

    with pytest.raises(UiOpenError, match="could not find Microsoft Edge"):
        opener.open("http://localhost:8765")

    assert browser_calls == []


def test_only_exact_discovered_browser_path_is_trusted() -> None:
    installed = (
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
    )

    assert _is_allowed_browser_process_path(
        Path("c:/program files/microsoft/edge/application/MSEDGE.EXE"),
        installed,
    )
    assert not _is_allowed_browser_process_path(
        Path("C:/Temp/msedge.exe"),
        installed,
    )
    assert not _is_allowed_browser_process_path(
        Path("C:/Program Files/Microsoft/Edge/Application/notepad.exe"),
        installed,
    )


def test_unmarked_matching_window_is_recovered_only_from_exact_local_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser = Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe")
    marker_restored = False

    def scan(
        _browsers: tuple[Path, ...],
        *,
        require_launcher_marker: bool = False,
    ) -> tuple[int, ...]:
        if require_launcher_marker:
            return (42,) if marker_restored else ()
        return (42,)

    def restore(window: int) -> bool:
        nonlocal marker_restored
        marker_restored = window == 42
        return marker_restored

    monkeypatch.setattr(
        "controlstudio_launcher.ui._controlstudio_windows",
        scan,
    )
    monkeypatch.setattr(
        "controlstudio_launcher.ui._window_shows_controlstudio_origin",
        lambda window: window == 42,
    )
    monkeypatch.setattr(
        "controlstudio_launcher.ui._set_launcher_owned_marker",
        restore,
    )

    assert _launcher_owned_controlstudio_windows((browser,)) == (42,)


def test_unmarked_lookalike_without_local_origin_remains_untrusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser = Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe")

    def scan(
        _browsers: tuple[Path, ...],
        *,
        require_launcher_marker: bool = False,
    ) -> tuple[int, ...]:
        return () if require_launcher_marker else (42,)

    monkeypatch.setattr(
        "controlstudio_launcher.ui._controlstudio_windows",
        scan,
    )
    monkeypatch.setattr(
        "controlstudio_launcher.ui._window_shows_controlstudio_origin",
        lambda _window: False,
    )

    with pytest.raises(OSError, match="not marked as launcher-owned"):
        _launcher_owned_controlstudio_windows((browser,))


def test_close_tracks_already_verified_hwnd_after_marker_disappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser = Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe")
    alive = True
    posted: list[int] = []

    monkeypatch.setattr(
        "controlstudio_launcher.ui._launcher_owned_controlstudio_windows",
        lambda _browsers: (42,),
    )
    monkeypatch.setattr(
        "controlstudio_launcher.ui._controlstudio_windows",
        lambda _browsers: (),
    )

    def post_close(window: int) -> bool:
        nonlocal alive
        posted.append(window)
        alive = False
        return True

    assert close_existing_controlstudio_windows(
        (browser,),
        post_close=post_close,
        window_exists=lambda _window: alive,
    )
    assert posted == [42]


def test_only_marked_matching_windows_are_returned_as_launcher_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser = Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe")
    monkeypatch.setattr(
        "controlstudio_launcher.ui._controlstudio_windows",
        lambda _browsers, **_kwargs: (42,),
    )

    assert _launcher_owned_controlstudio_windows((browser,)) == (42,)


def test_marked_window_remains_owned_when_its_page_title_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser = Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe")

    def scan(
        _browsers: tuple[Path, ...],
        *,
        require_launcher_marker: bool = False,
    ) -> tuple[int, ...]:
        return (42,) if require_launcher_marker else ()

    monkeypatch.setattr(
        "controlstudio_launcher.ui._controlstudio_windows",
        scan,
    )

    assert _launcher_owned_controlstudio_windows((browser,)) == (42,)


class _ForegroundUser32:
    def __init__(self, *, foreground_result: bool) -> None:
        self.foreground_result = foreground_result
        self.restored: list[int] = []
        self.flashed: list[int] = []

    def ShowWindow(self, window: int, _command: int) -> int:
        self.restored.append(window)
        return 0

    def SetForegroundWindow(self, _window: int) -> bool:
        return self.foreground_result

    def FlashWindow(self, window: int, _invert: bool) -> bool:
        self.flashed.append(window)
        return True


class _SwitchingForegroundUser32(_ForegroundUser32):
    def __init__(self) -> None:
        super().__init__(foreground_result=False)
        self.foreground_window = 0
        self.switched: list[int] = []

    def SwitchToThisWindow(self, window: int, _activate: bool) -> None:
        self.switched.append(window)
        self.foreground_window = window

    def GetForegroundWindow(self) -> int:
        return self.foreground_window


def test_foreground_failure_is_not_reported_as_success() -> None:
    user32 = _ForegroundUser32(foreground_result=False)

    assert not _focus_controlstudio_window(42, user32=user32)
    assert user32.restored == [42]
    assert user32.flashed == [42]


def test_foreground_uses_windows_switch_fallback_before_flashing() -> None:
    user32 = _SwitchingForegroundUser32()

    assert _focus_controlstudio_window(42, user32=user32)
    assert user32.restored == [42]
    assert user32.switched == [42]
    assert user32.flashed == []


def test_foreground_success_is_reported() -> None:
    user32 = _ForegroundUser32(foreground_result=True)

    assert _focus_controlstudio_window(42, user32=user32)
    assert user32.restored == [42]
    assert user32.flashed == []


def test_refresh_reloads_only_the_exact_foreground_window() -> None:
    sent: list[tuple[str, float]] = []

    assert _refresh_controlstudio_window(
        42,
        focus_window=lambda window: window == 42,
        foreground_window=lambda: 42,
        send_keys=lambda keys, pause: sent.append((keys, pause)),
    )
    assert sent == [("^r", 0.05)]


def test_refresh_refuses_global_input_when_foreground_changed() -> None:
    sent: list[str] = []

    assert not _refresh_controlstudio_window(
        42,
        focus_window=lambda _window: True,
        foreground_window=lambda: 84,
        send_keys=lambda keys, **_kwargs: sent.append(keys),
    )
    assert sent == []


def test_window_waiter_refreshes_one_white_app_window_then_requires_rendered_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser = Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe")
    now = 0.0
    probes = iter([False, False, False, True])
    refreshes: list[int] = []

    def monotonic() -> float:
        return now

    def sleep(seconds: float) -> None:
        nonlocal now
        now += max(seconds, 1.0)

    monkeypatch.setattr(
        "controlstudio_launcher.ui._controlstudio_windows",
        lambda _browsers, **_kwargs: (42,),
    )
    monkeypatch.setattr(
        "controlstudio_launcher.ui._set_launcher_owned_marker",
        lambda window: window == 42,
    )
    monkeypatch.setattr(
        "controlstudio_launcher.ui._focus_controlstudio_window",
        lambda window: window == 42,
    )

    assert wait_for_single_controlstudio_window(
        browser,
        timeout_seconds=10.0,
        monotonic=monotonic,
        sleep=sleep,
        rendered_probe=lambda _window: next(probes),
        refresher=lambda window: refreshes.append(window) or True,
    )
    assert refreshes == [42]
