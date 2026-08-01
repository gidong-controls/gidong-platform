from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path


class NativeFileDialogError(RuntimeError):
    """The operating system file dialog could not return a safe selection."""


class NativeFileDialogUnavailable(NativeFileDialogError):
    """The native dialog is not available on this operating system."""


class _OpenFileNameW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD),
        ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("lpstrFilter", wintypes.LPCWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR),
        ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD),
        ("lpstrFile", wintypes.LPWSTR),
        ("nMaxFile", wintypes.DWORD),
        ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD),
        ("lpstrInitialDir", wintypes.LPCWSTR),
        ("lpstrTitle", wintypes.LPCWSTR),
        ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD),
        ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR),
        ("lCustData", wintypes.LPARAM),
        ("lpfnHook", wintypes.LPVOID),
        ("lpTemplateName", wintypes.LPCWSTR),
        ("pvReserved", wintypes.LPVOID),
        ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


_OFN_HIDEREADONLY = 0x00000004
_OFN_NOCHANGEDIR = 0x00000008
_OFN_PATHMUSTEXIST = 0x00000800
_OFN_FILEMUSTEXIST = 0x00001000
_OFN_EXPLORER = 0x00080000
_OFN_ENABLESIZING = 0x00800000
_OFN_DONTADDTORECENT = 0x02000000
_MAX_DIALOG_PATH = 32_768
_XG5000_SUFFIXES = {".xgw", ".xgwx"}
_XG5000_FILTER = (
    "XG5000 projects (*.xgw;*.xgwx)\0*.xgw;*.xgwx\0"
    "XGWX projects (*.xgwx)\0*.xgwx\0"
    "XGW projects (*.xgw)\0*.xgw\0\0"
)
_KNOWLEDGE_SUFFIXES = {".pdf", ".md", ".markdown", ".txt"}
_KNOWLEDGE_FILTER = (
    "Engineering knowledge (*.pdf;*.md;*.markdown;*.txt)\0"
    "*.pdf;*.md;*.markdown;*.txt\0"
    "PDF manuals (*.pdf)\0*.pdf\0"
    "Markdown and text (*.md;*.markdown;*.txt)\0*.md;*.markdown;*.txt\0\0"
)


def select_xg5000_project_file() -> str | None:
    """Open the Windows read-only file selector for an existing XG5000 project.

    ``None`` means that the user cancelled the dialog. The helper never opens or
    mutates the selected project; it only returns a canonical absolute path.
    """

    return _select_local_file(
        title="Select an existing XG5000 project",
        file_filter=_XG5000_FILTER,
        allowed_suffixes=_XG5000_SUFFIXES,
        default_extension="xgwx",
        unavailable_message="The XG5000 project picker is available only on Windows.",
        invalid_selection_message=(
            "The selected file is not an XG5000 .xgw or .xgwx project."
        ),
    )


def select_knowledge_document_file() -> str | None:
    """Select one local manual, standard, or project document to import."""

    return _select_local_file(
        title="Import engineering knowledge",
        file_filter=_KNOWLEDGE_FILTER,
        allowed_suffixes=_KNOWLEDGE_SUFFIXES,
        default_extension="pdf",
        unavailable_message="The knowledge document picker is available only on Windows.",
        invalid_selection_message=(
            "The selected file is not a PDF, Markdown, or plain-text document."
        ),
    )


def _select_local_file(
    *,
    title: str,
    file_filter: str,
    allowed_suffixes: set[str],
    default_extension: str,
    unavailable_message: str,
    invalid_selection_message: str,
) -> str | None:
    if os.name != "nt":
        raise NativeFileDialogUnavailable(unavailable_message)

    comdlg32 = ctypes.WinDLL("comdlg32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_open_file_name = comdlg32.GetOpenFileNameW
    get_open_file_name.argtypes = [ctypes.POINTER(_OpenFileNameW)]
    get_open_file_name.restype = wintypes.BOOL
    extended_error = comdlg32.CommDlgExtendedError
    extended_error.argtypes = []
    extended_error.restype = wintypes.DWORD
    get_foreground_window = user32.GetForegroundWindow
    get_foreground_window.argtypes = []
    get_foreground_window.restype = wintypes.HWND

    path_buffer = ctypes.create_unicode_buffer(_MAX_DIALOG_PATH)
    dialog = _OpenFileNameW()
    dialog.lStructSize = ctypes.sizeof(_OpenFileNameW)
    dialog.hwndOwner = get_foreground_window()
    dialog.lpstrFilter = file_filter
    dialog.nFilterIndex = 1
    dialog.lpstrFile = ctypes.cast(path_buffer, wintypes.LPWSTR)
    dialog.nMaxFile = len(path_buffer)
    dialog.lpstrTitle = title
    dialog.Flags = (
        _OFN_HIDEREADONLY
        | _OFN_NOCHANGEDIR
        | _OFN_PATHMUSTEXIST
        | _OFN_FILEMUSTEXIST
        | _OFN_EXPLORER
        | _OFN_ENABLESIZING
        | _OFN_DONTADDTORECENT
    )
    dialog.lpstrDefExt = default_extension

    if not get_open_file_name(ctypes.byref(dialog)):
        error_code = int(extended_error())
        if error_code == 0:
            return None
        raise NativeFileDialogError(
            f"The Windows file dialog failed (CommDlgExtendedError=0x{error_code:04X})."
        )

    selected = Path(path_buffer.value)
    if selected.suffix.casefold() not in allowed_suffixes:
        raise NativeFileDialogError(invalid_selection_message)
    try:
        return str(selected.resolve(strict=True))
    except OSError as exc:
        raise NativeFileDialogError(
            "The selected file no longer exists or cannot be resolved."
        ) from exc
