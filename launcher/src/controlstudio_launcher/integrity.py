from __future__ import annotations

import base64
import ctypes
import json
import os
import socket
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from .constants import CANONICAL_PORT
from .errors import ElevationError

_XG5000_EXECUTABLE = "xg5000.exe"
_SUPPORTED_XG5000_PATH = Path(r"C:\XG5000\XG5000.EXE")
_HIGH_INTEGRITY_RID = 0x3000
_TOKEN_ELEVATION_TYPE_DEFAULT = 1
_TOKEN_ELEVATION_TYPE_FULL = 2
_TOKEN_ELEVATION_TYPE_LIMITED = 3
ELEVATION_HANDOFF_ENVIRONMENT = "CONTROLSTUDIO_ELEVATION_HANDOFF_V1"
_ELEVATION_CONTEXT_ARGUMENT = "--elevation-context"
_ELEVATION_CONTEXT_SCHEMA = "controlstudio-elevation-context/v1"
_ELEVATION_CONTEXT_ENVIRONMENT = (
    "LOCALAPPDATA",
    "CONTROLSTUDIO_RELEASE_CHANNEL",
    "CONTROLSTUDIO_PRODUCT_RELEASE",
    "CONTROLSTUDIO_DEVELOPMENT_MODEL_BASE_URL",
    "CONTROLSTUDIO_DEVELOPMENT_MODEL_NAME",
    "CONTROLSTUDIO_DEVELOPMENT_MODEL_TIMEOUT_SECONDS",
    "CONTROLSTUDIO_AGENT_COMMAND",
    "CONTROLSTUDIO_MCP_COMMAND",
    "CONTROLSTUDIO_XG5000_CLI_COMMAND",
)
_MAX_ELEVATION_CONTEXT_BYTES = 32 * 1024


class ElevationBroker(Protocol):
    def relaunch_if_required(
        self,
        *,
        verified_host: bool,
        parent_opens_ui: bool,
    ) -> bool: ...


class NoopElevationBroker:
    def relaunch_if_required(
        self,
        *,
        verified_host: bool,
        parent_opens_ui: bool,
    ) -> bool:
        return False


class WindowsElevationBroker:
    """Start the Host at the integrity required by installed XG5000 automation."""

    def __init__(
        self,
        arguments: Sequence[str],
        *,
        platform_name: str = os.name,
        process_integrity: Callable[[int], int] | None = None,
        xg5000_processes: Callable[[], tuple[int, ...]] | None = None,
        host_processes: Callable[[], tuple[int, ...]] | None = None,
        installed_xg5000_target: Callable[[int], int] | None = None,
        relaunch: Callable[[Sequence[str]], None] | None = None,
    ) -> None:
        self._arguments = tuple(arguments)
        self._platform_name = platform_name
        self._process_integrity = process_integrity or _process_integrity_level
        self._xg5000_processes = xg5000_processes or _current_session_xg5000_pids
        self._host_processes = host_processes or _canonical_listener_pids
        self._installed_xg5000_target = (
            installed_xg5000_target or _installed_xg5000_target_integrity
        )
        self._relaunch = relaunch or _relaunch_current_launcher_elevated

    def relaunch_if_required(
        self,
        *,
        verified_host: bool,
        parent_opens_ui: bool,
    ) -> bool:
        if self._platform_name != "nt":
            return False

        try:
            current_integrity = self._process_integrity(os.getpid())
            xg5000_processes = self._xg5000_processes()
        except OSError as exc:
            raise ElevationError(
                f"could not verify the XG5000/ControlStudio privilege boundary: {exc}"
            ) from exc
        if xg5000_processes:
            xg5000_integrities: list[int] = []
            for pid in xg5000_processes:
                try:
                    xg5000_integrities.append(self._process_integrity(pid))
                except OSError:
                    # A same-session XG5000 whose token cannot be queried from a
                    # medium-integrity launcher must not be automated blindly.
                    xg5000_integrities.append(_HIGH_INTEGRITY_RID)
            target_integrity = max(xg5000_integrities)
        else:
            try:
                target_integrity = self._installed_xg5000_target(current_integrity)
            except OSError as exc:
                raise ElevationError(
                    f"could not verify the installed XG5000 privilege boundary: {exc}"
                ) from exc

        if verified_host:
            try:
                host_processes = self._host_processes()
                host_integrities = [self._process_integrity(pid) for pid in host_processes]
            except OSError as exc:
                raise ElevationError(
                    f"could not verify the running ControlStudio Host privilege: {exc}"
                ) from exc
            if not host_integrities:
                raise ElevationError(
                    "the verified ControlStudio Host listener owner could not be identified"
                )
            if any(host_integrity < target_integrity for host_integrity in host_integrities):
                raise ElevationError(
                    "ControlStudio Host is running below the integrity required for "
                    "XG5000 automation. "
                    "Close ControlStudio Host and launch ControlStudio again so Windows "
                    "can request the matching privilege level."
                )
            return False

        if target_integrity > current_integrity:
            arguments = self._arguments
            if parent_opens_ui:
                # The medium-integrity parent owns every UI mode after the elevated
                # child has made Host ready.  Remove any parent browser selection so
                # the child receives exactly one non-opening mode.
                arguments = tuple(
                    argument
                    for argument in arguments
                    if argument not in {"--web", "--web-url"}
                )
            if parent_opens_ui and "--no-open" not in arguments:
                arguments = (*arguments, "--no-open")
            self._relaunch(_arguments_with_elevation_context(arguments))
            return True
        return False


def create_elevation_broker(arguments: Sequence[str]) -> ElevationBroker:
    if os.name == "nt":
        return WindowsElevationBroker(arguments)
    return NoopElevationBroker()


def encode_elevation_context(environ: dict[str, str] | None = None) -> str:
    """Carry only the fixed development launch context across ShellExecute.

    ``runas`` does not preserve the caller's modified environment.  The
    allowlist intentionally excludes API keys and arbitrary variable names.
    """

    values = os.environ if environ is None else environ
    environment = {
        name: value
        for name in _ELEVATION_CONTEXT_ENVIRONMENT
        if isinstance((value := values.get(name)), str)
        and value
        and "\x00" not in value
        and len(value) <= 4096
    }
    payload = {
        "schema": _ELEVATION_CONTEXT_SCHEMA,
        "environment": environment,
    }
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )
    if len(raw) > _MAX_ELEVATION_CONTEXT_BYTES:
        raise ElevationError("the fixed elevation context is too large")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def apply_elevation_context(encoded: str, environ: dict[str, str] | None = None) -> None:
    """Validate and consume the explicit elevated-child launch context."""

    if not isinstance(encoded, str) or not encoded or len(encoded) > 64 * 1024:
        raise ElevationError("the elevation context is invalid")
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("ascii"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ElevationError("the elevation context is invalid") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "environment"}
        or payload.get("schema") != _ELEVATION_CONTEXT_SCHEMA
        or not isinstance(payload.get("environment"), dict)
    ):
        raise ElevationError("the elevation context contract is invalid")
    supplied = payload["environment"]
    if not set(supplied).issubset(_ELEVATION_CONTEXT_ENVIRONMENT):
        raise ElevationError("the elevation context contains an unsupported setting")
    target = os.environ if environ is None else environ
    for name, value in supplied.items():
        if (
            not isinstance(value, str)
            or not value
            or "\x00" in value
            or len(value) > 4096
        ):
            raise ElevationError("the elevation context contains an invalid value")
        if name == "LOCALAPPDATA":
            path = Path(value)
            if not path.is_absolute() or not path.is_dir():
                raise ElevationError("the elevation context local app-data root is invalid")
            value = str(path.resolve())
        target[name] = value
    target[ELEVATION_HANDOFF_ENVIRONMENT] = "1"


def _installed_xg5000_target_integrity(current_integrity: int) -> int:
    if not _SUPPORTED_XG5000_PATH.is_file():
        return current_integrity
    elevation_type = _current_token_elevation_type()
    if elevation_type == _TOKEN_ELEVATION_TYPE_LIMITED:
        return max(current_integrity, _HIGH_INTEGRITY_RID)
    if elevation_type == _TOKEN_ELEVATION_TYPE_FULL:
        return max(current_integrity, _HIGH_INTEGRITY_RID)
    if elevation_type == _TOKEN_ELEVATION_TYPE_DEFAULT:
        return current_integrity
    raise OSError("Windows returned an unknown token elevation type")


def _current_token_elevation_type() -> int:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = ()
    get_current_process.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    open_process_token = advapi32.OpenProcessToken
    open_process_token.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    open_process_token.restype = wintypes.BOOL
    get_token_information = advapi32.GetTokenInformation
    get_token_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    get_token_information.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not open_process_token(get_current_process(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        elevation_type = wintypes.DWORD()
        returned = wintypes.DWORD()
        if not get_token_information(
            token,
            18,  # TokenElevationType
            ctypes.byref(elevation_type),
            ctypes.sizeof(elevation_type),
            ctypes.byref(returned),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(elevation_type.value)
    finally:
        close_handle(token)


def _relaunch_current_launcher_elevated(arguments: Sequence[str]) -> None:
    target, target_arguments = _current_launcher_command(arguments)
    parameters = subprocess.list2cmdline(list(target_arguments))
    result_value = _shell_execute_runas(target, parameters)
    if result_value <= 32:
        raise ElevationError(
            "XG5000 automation requires ControlStudio to use the vendor integrity "
            "level; elevation was not approved."
        )


def _arguments_with_elevation_context(arguments: Sequence[str]) -> tuple[str, ...]:
    filtered: list[str] = []
    skip = False
    for argument in arguments:
        if skip:
            skip = False
            continue
        if argument == _ELEVATION_CONTEXT_ARGUMENT:
            skip = True
            continue
        filtered.append(argument)
    context = encode_elevation_context()
    return (*filtered, _ELEVATION_CONTEXT_ARGUMENT, context)


def _shell_execute_runas(target: str, parameters: str) -> int:
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell_execute = shell32.ShellExecuteW
    shell_execute.argtypes = (
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_int,
    )
    shell_execute.restype = ctypes.c_void_p
    result = shell_execute(None, "runas", target, parameters, None, 1)
    return int(result or 0)


def _current_launcher_command(arguments: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    argv0 = Path(sys.argv[0])
    if argv0.suffix.casefold() == ".exe":
        return str(argv0.resolve()), tuple(arguments)
    return sys.executable, ("-m", "controlstudio_launcher", *arguments)


def _current_session_xg5000_pids() -> tuple[int, ...]:
    from ctypes import wintypes

    class _ProcessEntry32W(ctypes.Structure):
        _fields_ = (
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    current_session = _process_session_id(os.getpid())
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    create_snapshot.restype = wintypes.HANDLE
    snapshot = create_snapshot(0x00000002, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot == invalid_handle:
        raise ctypes.WinError(ctypes.get_last_error())

    entry = _ProcessEntry32W()
    entry.dwSize = ctypes.sizeof(entry)
    process_first = kernel32.Process32FirstW
    process_first.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W))
    process_first.restype = wintypes.BOOL
    process_next = kernel32.Process32NextW
    process_next.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W))
    process_next.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    pids: list[int] = []
    try:
        has_entry = bool(process_first(snapshot, ctypes.byref(entry)))
        while has_entry:
            pid = int(entry.th32ProcessID)
            if entry.szExeFile.casefold() == _XG5000_EXECUTABLE:
                try:
                    same_session = _process_session_id(pid) == current_session
                except OSError:
                    # The process may have exited between the snapshot and the
                    # session lookup.
                    same_session = False
                if same_session:
                    pids.append(pid)
            has_entry = bool(process_next(snapshot, ctypes.byref(entry)))
    finally:
        close_handle(snapshot)
    return tuple(pids)


def _process_session_id(pid: int) -> int:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    process_id_to_session_id = kernel32.ProcessIdToSessionId
    process_id_to_session_id.argtypes = (wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    process_id_to_session_id.restype = wintypes.BOOL
    session_id = wintypes.DWORD()
    if not process_id_to_session_id(pid, ctypes.byref(session_id)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(session_id.value)


def _process_integrity_level(pid: int) -> int:
    from ctypes import wintypes

    class _SidAndAttributes(ctypes.Structure):
        _fields_ = (("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD))

    class _TokenMandatoryLabel(ctypes.Structure):
        _fields_ = (("Label", _SidAndAttributes),)

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    open_process_token = advapi32.OpenProcessToken
    open_process_token.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    open_process_token.restype = wintypes.BOOL
    get_token_information = advapi32.GetTokenInformation
    get_token_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    get_token_information.restype = wintypes.BOOL
    get_sid_sub_authority_count = advapi32.GetSidSubAuthorityCount
    get_sid_sub_authority_count.argtypes = (ctypes.c_void_p,)
    get_sid_sub_authority_count.restype = ctypes.POINTER(ctypes.c_ubyte)
    get_sid_sub_authority = advapi32.GetSidSubAuthority
    get_sid_sub_authority.argtypes = (ctypes.c_void_p, wintypes.DWORD)
    get_sid_sub_authority.restype = ctypes.POINTER(wintypes.DWORD)

    process = open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())
    token = wintypes.HANDLE()
    try:
        if not open_process_token(process, 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
            raise ctypes.WinError(ctypes.get_last_error())
        required = wintypes.DWORD()
        get_token_information(token, 25, None, 0, ctypes.byref(required))
        if not required.value:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(required.value)
        if not get_token_information(
            token,
            25,  # TokenIntegrityLevel
            buffer,
            required.value,
            ctypes.byref(required),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        label = ctypes.cast(buffer, ctypes.POINTER(_TokenMandatoryLabel)).contents
        count_pointer = get_sid_sub_authority_count(label.Label.Sid)
        if not count_pointer:
            raise ctypes.WinError(ctypes.get_last_error())
        sub_authority_count = int(count_pointer.contents.value)
        rid_pointer = get_sid_sub_authority(
            label.Label.Sid,
            sub_authority_count - 1,
        )
        if not rid_pointer:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(rid_pointer.contents.value)
    finally:
        if token:
            close_handle(token)
        close_handle(process)


def _canonical_listener_pids() -> tuple[int, ...]:
    """Return PIDs listening on the canonical IPv4 loopback port."""

    from ctypes import wintypes

    class _MibTcpRowOwnerPid(ctypes.Structure):
        _fields_ = (
            ("dwState", wintypes.DWORD),
            ("dwLocalAddr", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("dwRemoteAddr", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        )

    iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
    get_table = iphlpapi.GetExtendedTcpTable
    get_table.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
        wintypes.ULONG,
        ctypes.c_int,
        wintypes.ULONG,
    )
    get_table.restype = wintypes.DWORD
    size = wintypes.DWORD()
    result = get_table(None, ctypes.byref(size), False, 2, 3, 0)
    if result not in {0, 122}:  # NO_ERROR / ERROR_INSUFFICIENT_BUFFER
        raise ctypes.WinError(result)
    buffer = ctypes.create_string_buffer(size.value)
    result = get_table(buffer, ctypes.byref(size), False, 2, 3, 0)
    if result != 0:
        raise ctypes.WinError(result)

    count = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD)).contents.value
    first_row_address = ctypes.addressof(buffer) + ctypes.sizeof(wintypes.DWORD)
    rows = ctypes.cast(
        first_row_address,
        ctypes.POINTER(_MibTcpRowOwnerPid * count),
    ).contents
    pids = {
        int(row.dwOwningPid)
        for row in rows
        if row.dwState == 2  # MIB_TCP_STATE_LISTEN
        and socket.ntohs(row.dwLocalPort & 0xFFFF) == CANONICAL_PORT
        and row.dwLocalAddr in {0, 0x0100007F}  # 0.0.0.0 / 127.0.0.1
    }
    return tuple(sorted(pids))
