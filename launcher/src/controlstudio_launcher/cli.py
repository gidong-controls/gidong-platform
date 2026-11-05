from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from .app import ControlStudioLauncher
from .config import (
    LauncherSettings,
    apply_cli_overrides,
    apply_config,
    default_config_path,
    default_settings,
    load_config_file,
)
from .errors import (
    ConfigurationError,
    HostStartError,
    LauncherAlreadyRunning,
    UiOpenError,
    UnexpectedServiceError,
)
from .health import HttpHealthProbe
from .host import SubprocessHostStarter
from .instance import create_instance_guard
from .integrity import apply_elevation_context, create_elevation_broker
from .token import FileLaunchTokenStore
from .ui import SystemUiOpener


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="controlstudio",
        description="Start the local ControlStudio Host and open its verified UI.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="local JSON config path (defaults to the current user's app-data directory)",
    )
    parser.add_argument(
        "--no-config",
        action="store_true",
        help="do not read the default local launcher config",
    )
    parser.add_argument(
        "--host-executable",
        help="local Host executable override; never accepted from the browser",
    )
    parser.add_argument(
        "--host-arg",
        action="append",
        default=[],
        help="Host argv item (repeatable; requires --host-executable)",
    )
    parser.add_argument("--host-cwd", help="absolute working directory for the Host")
    parser.add_argument(
        "--expected-host-build",
        help="exact Host build_id expected at /api/health",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        help="seconds to wait for the verified Host",
    )
    ui_mode = parser.add_mutually_exclusive_group()
    ui_mode.add_argument(
        "--no-open",
        action="store_true",
        help="verify/start the Host without opening a browser (local diagnostics only)",
    )
    ui_mode.add_argument(
        "--web",
        action="store_true",
        help="open the verified one-time handoff in the system web browser",
    )
    ui_mode.add_argument(
        "--web-url",
        action="store_true",
        help="write one verified one-time handoff URL to stdout without opening a browser",
    )
    parser.add_argument(
        "--web-url-file", type=Path,
        help="with --web-url, write the handoff privately to a new local file instead of stdout",
    )
    parser.add_argument(
        "--development-web", action="store_true",
        help="use the local Vite UI on port 5173 with --web or --web-url (development only)",
    )
    parser.add_argument("--elevation-context", help=argparse.SUPPRESS)
    return parser


def resolve_settings(
    arguments: argparse.Namespace,
    *,
    python_executable: str | None = None,
) -> LauncherSettings:
    settings = default_settings(python_executable=python_executable)
    config_path = arguments.config
    if config_path is not None:
        settings = apply_config(settings, load_config_file(config_path, required=True))
    elif not arguments.no_config:
        settings = apply_config(settings, load_config_file(default_config_path()))

    return apply_cli_overrides(
        settings,
        host_executable=arguments.host_executable,
        host_args=arguments.host_arg,
        host_cwd=arguments.host_cwd,
        expected_host_build=arguments.expected_host_build,
        startup_timeout_seconds=arguments.startup_timeout,
    )


def _web_handoff_path(path: Path) -> Path:
    """Accept an explicit new local destination; never create parent directories."""
    destination = path.absolute()
    if not destination.parent.is_dir():
        raise ConfigurationError("The web handoff destination requires an existing local parent directory")
    for ancestor in (destination, *destination.parents):
        if ancestor.is_symlink() or (hasattr(ancestor, "is_junction") and ancestor.is_junction()):
            raise ConfigurationError("The web handoff destination cannot traverse links or junctions")
    if destination.exists():
        raise ConfigurationError("The web handoff destination must be a new file")
    if os.name == "nt":
        import win32file
        if destination.drive.startswith("\\") or win32file.GetDriveType(destination.anchor) not in (3, 6):
            raise ConfigurationError("The web handoff destination must be on a local drive")
    return destination


def _write_web_handoff_file(destination: Path, url: str) -> None:
    """Exclusive private creation, with no credential-bearing stdout or error text."""
    _web_handoff_path(destination)  # Revalidate after Host/elevation readiness.
    payload = (url + "\n").encode("utf-8")
    try:
        if os.name == "nt":
            import pywintypes
            import win32api
            import win32con
            import win32file
            import win32security

            token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
            try:
                sid = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
            finally:
                token.Close()
            security = pywintypes.SECURITY_ATTRIBUTES()
            security.bInheritHandle = False
            security.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
                f"O:{sid}D:P(A;;FA;;;{sid})", win32security.SDDL_REVISION_1)
            handle = win32file.CreateFile(str(destination), win32con.GENERIC_WRITE, 0,
                security, win32con.CREATE_NEW, win32con.FILE_ATTRIBUTE_NORMAL, None)
            try:
                error, written = win32file.WriteFile(handle, payload)
                if error or written != len(payload):
                    raise OSError("Incomplete private handoff write")
                win32file.FlushFileBuffers(handle)
            finally:
                handle.Close()
        else:
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
    except Exception:
        # A partial file is retained and never overwritten or silently retried.
        raise UiOpenError("The private web handoff file could not be created completely") from None


def _without_web_url_file(arguments: Sequence[str]) -> tuple[str, ...]:
    """The parent owns this sink; the elevated --no-open child never receives it."""
    result: list[str] = []
    skip_value = False
    for argument in arguments:
        option = argument.split("=", 1)[0]
        is_sink_option = option.startswith("--web-url-") and "--web-url-file".startswith(option)
        if skip_value:
            skip_value = False
        elif is_sink_option:
            skip_value = "=" not in argument
        else:
            result.append(argument)
    return tuple(result)


def main(argv: Sequence[str] | None = None) -> int:
    return _main(argv, show_dialogs=False)


def gui_main(argv: Sequence[str] | None = None) -> int:
    """Windowed launcher entry point used by the installed ControlStudio icon."""

    return _main(argv, show_dialogs=True)


def _main(
    argv: Sequence[str] | None,
    *,
    show_dialogs: bool,
) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.elevation_context is not None:
            apply_elevation_context(arguments.elevation_context)
        handoff_path = None
        if arguments.web_url_file is not None:
            if not arguments.web_url:
                raise ConfigurationError("--web-url-file requires --web-url")
            handoff_path = _web_handoff_path(arguments.web_url_file)
        settings = resolve_settings(arguments)
        if settings.data_directory is not None:
            # An explicit profile selects all storage paths together, overriding
            # inherited individual paths before Host startup and elevation.
            os.environ["CONTROLSTUDIO_DATABASE"] = str(settings.data_directory / "controlstudio.sqlite3")
            os.environ["CONTROLSTUDIO_WORKSPACE_ROOT"] = str(settings.data_directory / "workspaces")
            os.environ["CONTROLSTUDIO_KNOWLEDGE_DATABASE"] = str(settings.data_directory / "controlstudio-knowledge.sqlite3")
        if arguments.development_web and (
            os.environ.get("CONTROLSTUDIO_RELEASE_CHANNEL") != "development"
            or not (arguments.web or arguments.web_url)
        ):
            raise ConfigurationError("--development-web requires the development channel and --web or --web-url")
        launcher = ControlStudioLauncher(
            settings=settings,
            health_probe=HttpHealthProbe(
                expected_build=settings.expected_host_build,
                timeout_seconds=settings.probe_timeout_seconds,
            ),
            host_starter=SubprocessHostStarter(),
            ui_opener=SystemUiOpener(),
            instance_guard=create_instance_guard(),
            token_store=FileLaunchTokenStore(),
            development_web=arguments.development_web,
            web_url_sink=(lambda url: _write_web_handoff_file(handoff_path, url)) if handoff_path is not None else None,
            elevation_broker=create_elevation_broker(
                _without_web_url_file(sys.argv[1:] if argv is None else argv)
            ),
        )
        if arguments.no_open:
            ui_mode = "none"
        elif arguments.web_url:
            ui_mode = "web-url"
        elif arguments.web:
            ui_mode = "web"
        else:
            ui_mode = "app"
        launcher.run(ui_mode=ui_mode)
    except ConfigurationError as exc:
        _report_error(
            f"ControlStudio launcher configuration error: {exc}",
            show_dialog=show_dialogs,
        )
        return 2
    except LauncherAlreadyRunning:
        # A double-click while the first launcher is still starting must not
        # start or race a second Host.
        return 0
    except UnexpectedServiceError as exc:
        _report_error(
            f"ControlStudio refused to open: {exc}",
            show_dialog=show_dialogs,
        )
        return 3
    except HostStartError as exc:
        _report_error(
            f"ControlStudio Host startup failed: {exc}",
            show_dialog=show_dialogs,
        )
        return 4
    except UiOpenError as exc:
        _report_error(str(exc), show_dialog=show_dialogs)
        return 5
    return 0


def _report_error(message: str, *, show_dialog: bool) -> None:
    if sys.stderr is not None:
        print(message, file=sys.stderr)
    if show_dialog and os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            message,
            "ControlStudio",
            0x00000010,
        )
