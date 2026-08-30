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
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="verify/start the Host without opening a browser (local diagnostics only)",
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
        settings = resolve_settings(arguments)
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
            elevation_broker=create_elevation_broker(
                tuple(sys.argv[1:] if argv is None else argv)
            ),
        )
        launcher.run(open_ui=not arguments.no_open)
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
