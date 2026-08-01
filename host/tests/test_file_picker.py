from __future__ import annotations

import ctypes
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from controlstudio_host import windows_dialogs
from controlstudio_host.windows_dialogs import (
    NativeFileDialogError,
    NativeFileDialogUnavailable,
    select_xg5000_project_file,
)


class _FakeWinFunction:
    def __init__(self, callback: Callable[..., Any]) -> None:
        self.callback = callback
        self.argtypes: list[Any] | None = None
        self.restype: Any = None

    def __call__(self, *args: Any) -> Any:
        return self.callback(*args)


def _pointer_field_value(structure: ctypes.Structure, field_name: str) -> int:
    field = getattr(type(structure), field_name)
    address = ctypes.addressof(structure) + field.offset
    pointer = ctypes.c_void_p.from_address(address).value
    assert pointer is not None
    return pointer


def _install_fake_windows_dialog(
    monkeypatch: pytest.MonkeyPatch,
    *,
    selected_path: Path | None = None,
    error_code: int = 0,
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def get_open_file_name(pointer: Any) -> int:
        dialog = pointer._obj
        captured["dialog"] = dialog
        filter_pointer = _pointer_field_value(dialog, "lpstrFilter")
        captured["filter"] = ctypes.wstring_at(
            filter_pointer,
            len(windows_dialogs._XG5000_FILTER),
        )
        if selected_path is None or error_code:
            return 0
        value = ctypes.create_unicode_buffer(str(selected_path))
        file_pointer = _pointer_field_value(dialog, "lpstrFile")
        ctypes.memmove(file_pointer, value, ctypes.sizeof(value))
        return 1

    get_open = _FakeWinFunction(get_open_file_name)
    extended_error = _FakeWinFunction(lambda: error_code)
    foreground_window = _FakeWinFunction(lambda: 12345)
    libraries = {
        "comdlg32": SimpleNamespace(
            GetOpenFileNameW=get_open,
            CommDlgExtendedError=extended_error,
        ),
        "user32": SimpleNamespace(GetForegroundWindow=foreground_window),
    }

    def fake_win_dll(name: str, *, use_last_error: bool) -> Any:
        assert use_last_error is True
        return libraries[name]

    monkeypatch.setattr(windows_dialogs.os, "name", "nt")
    monkeypatch.setattr(
        windows_dialogs.ctypes,
        "WinDLL",
        fake_win_dll,
        raising=False,
    )
    return captured


def _csrf_token(client: TestClient) -> str:
    response = client.get("/api/v1/bootstrap")
    assert response.status_code == 200
    token = response.json()["csrfToken"]
    assert isinstance(token, str)
    assert token
    return token


def test_file_picker_requires_authenticated_browser_session(client: TestClient) -> None:
    response = client.post(
        "/api/v1/files/select-xg5000-project",
        headers={"Authorization": ""},
    )

    assert response.status_code == 401


def test_file_picker_requires_matching_csrf_token(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def unexpected_dialog() -> str:
        nonlocal called
        called = True
        return r"C:\projects\machine.xgwx"

    monkeypatch.setattr(
        "controlstudio_host.app.select_xg5000_project_file",
        unexpected_dialog,
    )

    missing = client.post("/api/v1/files/select-xg5000-project")
    invalid = client.post(
        "/api/v1/files/select-xg5000-project",
        headers={"X-ControlStudio-CSRF": "invalid"},
    )

    assert missing.status_code == 403
    assert invalid.status_code == 403
    assert called is False


def test_file_picker_returns_selected_xg5000_path(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_path = r"C:\projects\Sample machine.xgwx"
    monkeypatch.setattr(
        "controlstudio_host.app.select_xg5000_project_file",
        lambda: selected_path,
    )

    response = client.post(
        "/api/v1/files/select-xg5000-project",
        headers={"X-ControlStudio-CSRF": _csrf_token(client)},
    )

    assert response.status_code == 200
    assert response.json() == {
        "selected": True,
        "path": selected_path,
    }


def test_file_picker_cancel_is_not_an_error(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "controlstudio_host.app.select_xg5000_project_file",
        lambda: None,
    )

    response = client.post(
        "/api/v1/files/select-xg5000-project",
        headers={"X-ControlStudio-CSRF": _csrf_token(client)},
    )

    assert response.status_code == 200
    assert response.json() == {
        "selected": False,
        "path": None,
    }


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (
            NativeFileDialogUnavailable(
                "The XG5000 project picker is available only on Windows."
            ),
            501,
        ),
        (
            NativeFileDialogError(
                "The Windows file dialog failed (CommDlgExtendedError=0x0002)."
            ),
            503,
        ),
    ],
)
def test_file_picker_reports_native_dialog_failures(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error: NativeFileDialogError,
    expected_status: int,
) -> None:
    def failing_dialog() -> str | None:
        raise error

    monkeypatch.setattr(
        "controlstudio_host.app.select_xg5000_project_file",
        failing_dialog,
    )

    response = client.post(
        "/api/v1/files/select-xg5000-project",
        headers={"X-ControlStudio-CSRF": _csrf_token(client)},
    )

    assert response.status_code == expected_status
    assert response.json() == {"detail": str(error)}


def test_native_file_picker_fails_closed_off_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("controlstudio_host.windows_dialogs.os.name", "posix")

    with pytest.raises(NativeFileDialogUnavailable, match="only on Windows"):
        select_xg5000_project_file()


@pytest.mark.parametrize("suffix", [".xgw", ".XGWX"])
def test_native_file_picker_uses_bounded_xg5000_filter_and_returns_canonical_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    project = tmp_path / f"machine{suffix}"
    project.write_bytes(b"xg5000-project")
    intermediate = tmp_path / "existing-folder"
    intermediate.mkdir()
    dialog_path = intermediate / ".." / project.name
    captured = _install_fake_windows_dialog(
        monkeypatch,
        selected_path=dialog_path,
    )

    result = select_xg5000_project_file()

    assert result == str(project.resolve(strict=True))
    assert captured["filter"] == windows_dialogs._XG5000_FILTER
    assert "*.xgw;*.xgwx" in captured["filter"]
    dialog = captured["dialog"]
    assert dialog.lStructSize == ctypes.sizeof(windows_dialogs._OpenFileNameW)
    assert dialog.hwndOwner == 12345
    assert dialog.nMaxFile == windows_dialogs._MAX_DIALOG_PATH
    assert dialog.nFilterIndex == 1
    assert dialog.lpstrDefExt == "xgwx"
    assert dialog.Flags & windows_dialogs._OFN_FILEMUSTEXIST
    assert dialog.Flags & windows_dialogs._OFN_PATHMUSTEXIST
    assert dialog.Flags & windows_dialogs._OFN_NOCHANGEDIR
    assert dialog.Flags & windows_dialogs._OFN_DONTADDTORECENT


def test_native_file_picker_maps_windows_cancel_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_windows_dialog(monkeypatch)

    assert select_xg5000_project_file() is None
    assert captured["dialog"].nMaxFile == windows_dialogs._MAX_DIALOG_PATH


def test_native_file_picker_reports_windows_extended_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_windows_dialog(monkeypatch, error_code=0x0002)

    with pytest.raises(
        NativeFileDialogError,
        match=r"CommDlgExtendedError=0x0002",
    ):
        select_xg5000_project_file()


def test_native_file_picker_rejects_non_xg5000_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "machine.txt"
    selected.write_text("not-an-xg5000-project", encoding="utf-8")
    _install_fake_windows_dialog(monkeypatch, selected_path=selected)

    with pytest.raises(
        NativeFileDialogError,
        match=r"not an XG5000 \.xgw or \.xgwx project",
    ):
        select_xg5000_project_file()


def test_native_file_picker_requires_selected_file_to_still_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing.xgwx"
    _install_fake_windows_dialog(monkeypatch, selected_path=missing)

    with pytest.raises(
        NativeFileDialogError,
        match="no longer exists or cannot be resolved",
    ):
        select_xg5000_project_file()
