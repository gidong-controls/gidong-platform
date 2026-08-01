from __future__ import annotations

import os

import pytest

from controlstudio_launcher.errors import ElevationError
from controlstudio_launcher.integrity import (
    WindowsElevationBroker,
    _relaunch_current_launcher_elevated,
)


def test_elevated_xg5000_relaunches_launcher_once() -> None:
    relaunched: list[tuple[str, ...]] = []
    integrity = {os.getpid(): 0x2000, 42: 0x3000}
    broker = WindowsElevationBroker(
        ("--no-config",),
        platform_name="nt",
        process_integrity=integrity.__getitem__,
        xg5000_processes=lambda: (42,),
        relaunch=lambda arguments: relaunched.append(tuple(arguments)),
    )

    assert broker.relaunch_if_required(verified_host=False, parent_opens_ui=True)
    assert relaunched == [("--no-config", "--no-open")]


def test_same_integrity_xg5000_does_not_prompt() -> None:
    relaunched: list[tuple[str, ...]] = []
    integrity = {os.getpid(): 0x2000, 42: 0x2000}
    broker = WindowsElevationBroker(
        (),
        platform_name="nt",
        process_integrity=integrity.__getitem__,
        xg5000_processes=lambda: (42,),
        relaunch=lambda arguments: relaunched.append(tuple(arguments)),
    )

    assert not broker.relaunch_if_required(
        verified_host=False,
        parent_opens_ui=False,
    )
    assert relaunched == []


def test_already_elevated_launcher_does_not_relaunch() -> None:
    relaunched: list[tuple[str, ...]] = []
    broker = WindowsElevationBroker(
        (),
        platform_name="nt",
        process_integrity=lambda _pid: 0x3000,
        xg5000_processes=lambda: (42,),
        relaunch=lambda arguments: relaunched.append(tuple(arguments)),
    )

    assert not broker.relaunch_if_required(
        verified_host=False,
        parent_opens_ui=False,
    )
    assert relaunched == []


def test_unqueryable_xg5000_fails_safe_by_requesting_elevation() -> None:
    relaunched: list[tuple[str, ...]] = []

    def process_integrity(pid: int) -> int:
        if pid == os.getpid():
            return 0x2000
        raise PermissionError("token access denied")

    broker = WindowsElevationBroker(
        (),
        platform_name="nt",
        process_integrity=process_integrity,
        xg5000_processes=lambda: (42,),
        relaunch=lambda arguments: relaunched.append(tuple(arguments)),
    )

    assert broker.relaunch_if_required(verified_host=False, parent_opens_ui=False)
    assert relaunched == [()]


def test_non_windows_launcher_never_requests_elevation() -> None:
    broker = WindowsElevationBroker(
        (),
        platform_name="posix",
        process_integrity=lambda _pid: (_ for _ in ()).throw(AssertionError()),
        xg5000_processes=lambda: (_ for _ in ()).throw(AssertionError()),
        relaunch=lambda _arguments: (_ for _ in ()).throw(AssertionError()),
    )

    assert not broker.relaunch_if_required(
        verified_host=False,
        parent_opens_ui=False,
    )


def test_integrity_preflight_failure_is_reported_as_launcher_error() -> None:
    broker = WindowsElevationBroker(
        (),
        platform_name="nt",
        process_integrity=lambda _pid: (_ for _ in ()).throw(OSError("token failed")),
        xg5000_processes=lambda: (),
        relaunch=lambda _arguments: None,
    )

    with pytest.raises(ElevationError, match="privilege boundary"):
        broker.relaunch_if_required(verified_host=False, parent_opens_ui=False)


def test_matching_elevated_host_is_reused_by_medium_ui_parent() -> None:
    relaunched: list[tuple[str, ...]] = []
    integrity = {os.getpid(): 0x2000, 42: 0x3000, 99: 0x3000}
    broker = WindowsElevationBroker(
        (),
        platform_name="nt",
        process_integrity=integrity.__getitem__,
        xg5000_processes=lambda: (42,),
        host_processes=lambda: (99,),
        relaunch=lambda arguments: relaunched.append(tuple(arguments)),
    )

    assert not broker.relaunch_if_required(
        verified_host=True,
        parent_opens_ui=True,
    )
    assert relaunched == []


def test_lower_integrity_ready_host_is_rejected_for_elevated_xg5000() -> None:
    integrity = {os.getpid(): 0x2000, 42: 0x3000, 99: 0x2000}
    broker = WindowsElevationBroker(
        (),
        platform_name="nt",
        process_integrity=integrity.__getitem__,
        xg5000_processes=lambda: (42,),
        host_processes=lambda: (99,),
        relaunch=lambda _arguments: None,
    )

    with pytest.raises(ElevationError, match="lower privileges than XG5000"):
        broker.relaunch_if_required(
            verified_host=True,
            parent_opens_ui=True,
        )


def test_cancelled_uac_is_reported_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "controlstudio_launcher.integrity._current_launcher_command",
        lambda _arguments: ("controlstudio.exe", ("--no-open",)),
    )
    monkeypatch.setattr(
        "controlstudio_launcher.integrity._shell_execute_runas",
        lambda _target, _parameters: 5,  # SE_ERR_ACCESSDENIED / cancelled consent
    )

    with pytest.raises(ElevationError, match="elevation was not approved"):
        _relaunch_current_launcher_elevated(("--no-open",))
