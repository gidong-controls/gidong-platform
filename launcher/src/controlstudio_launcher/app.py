from __future__ import annotations

import os
import time
from collections.abc import Callable
from urllib.parse import quote

from .config import LauncherSettings
from .constants import CANONICAL_BASE_URL
from .elevation_receipt import ElevationReceiptStore
from .errors import HostStartError, LauncherAlreadyRunning, UnexpectedServiceError
from .health import HealthProbe, HealthResult, HealthState
from .host import (
    HostProcess,
    HostStarter,
    release_process_started_by_launcher,
    stop_process_started_by_launcher,
    write_launcher_diagnostic,
)
from .instance import InstanceGuard
from .integrity import (
    ELEVATION_HANDOFF_ENVIRONMENT,
    ElevationBroker,
    NoopElevationBroker,
)
from .token import LaunchTokenStore
from .ui import UiOpener

_HOST_ONLY_DEVELOPMENT_MODEL_ENVIRONMENT = (
    "CONTROLSTUDIO_DEVELOPMENT_MODEL_BASE_URL",
    "CONTROLSTUDIO_DEVELOPMENT_MODEL_NAME",
    "CONTROLSTUDIO_DEVELOPMENT_MODEL_TIMEOUT_SECONDS",
    "CONTROLSTUDIO_DEVELOPMENT_MODEL_API_KEY",
)


def _take_host_only_environment() -> dict[str, str]:
    result: dict[str, str] = {}
    for name in _HOST_ONLY_DEVELOPMENT_MODEL_ENVIRONMENT:
        value = os.environ.pop(name, None)
        if value is not None:
            result[name] = value
    return result


class ControlStudioLauncher:
    def __init__(
        self,
        *,
        settings: LauncherSettings,
        health_probe: HealthProbe,
        host_starter: HostStarter,
        ui_opener: UiOpener,
        instance_guard: InstanceGuard,
        token_store: LaunchTokenStore,
        elevation_broker: ElevationBroker | None = None,
        elevation_receipts: ElevationReceiptStore | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._health_probe = health_probe
        self._host_starter = host_starter
        self._ui_opener = ui_opener
        self._instance_guard = instance_guard
        self._token_store = token_store
        self._elevation_broker = elevation_broker or NoopElevationBroker()
        self._elevation_receipts = elevation_receipts or ElevationReceiptStore()
        self._monotonic = monotonic
        self._sleep = sleep

    def run(self, *, open_ui: bool = True) -> None:
        # Elevation must be handed off before taking the launcher mutex. If the
        # medium-integrity parent held it while invoking ``runas``, the elevated
        # child would observe "already running" and exit before starting Host.
        elevation_probe = self._health_probe.probe()
        verified_host = (
            elevation_probe.state is HealthState.READY
            or (
                elevation_probe.state is HealthState.STARTING
                and elevation_probe.payload is not None
            )
        )
        elevated_handoff_child = os.environ.pop(ELEVATION_HANDOFF_ENVIRONMENT, None) == "1"
        observation = self._elevation_receipts.begin(
            initial_host_state=elevation_probe.state.value,
            initial_host_verified=verified_host,
            role="elevated_handoff_child" if elevated_handoff_child else "launcher",
        )
        if not elevated_handoff_child:
            os.environ[ELEVATION_HANDOFF_ENVIRONMENT] = "1"
        try:
            elevation_relaunch_requested = self._elevation_broker.relaunch_if_required(
                verified_host=verified_host,
                parent_opens_ui=open_ui,
            )
        except BaseException:
            self._elevation_receipts.finish(
                observation,
                elevation_relaunch_requested=False,
                host_ready=False,
                elevated_host_retention_verified=False,
                outcome="failed",
            )
            raise
        finally:
            if not elevated_handoff_child:
                os.environ.pop(ELEVATION_HANDOFF_ENVIRONMENT, None)
        if elevation_relaunch_requested:
            _take_host_only_environment()
            write_launcher_diagnostic(
                "elevation handoff started before launcher instance lock"
            )
            try:
                self._wait_until_ready(None)
                launch_token = self._token_store.load()
                if launch_token is None:
                    raise HostStartError(
                        "elevated Host became ready but its launch session token "
                        "is unavailable"
                    )
                self._verify_launch_token_until_ready(launch_token, None)
                self._verify_elevated_host_retention()
                if open_ui:
                    self._open_elevation_handoff_under_instance_guard(launch_token)
                self._elevation_receipts.finish(
                    observation,
                    elevation_relaunch_requested=True,
                    host_ready=True,
                    elevated_host_retention_verified=True,
                    outcome="ready",
                )
                return
            except Exception:
                self._elevation_receipts.finish(
                    observation,
                    elevation_relaunch_requested=True,
                    host_ready=False,
                    elevated_host_retention_verified=False,
                    outcome="failed",
                )
                raise

        host_only_environment = _take_host_only_environment()

        with self._instance_guard:
            initial = self._health_probe.probe()
            write_launcher_diagnostic(
                f"health initial state={initial.state.value} detail={initial.detail!r}"
            )
            process: HostProcess | None = None
            launch_succeeded = False
            try:
                if initial.state is HealthState.UNEXPECTED:
                    raise UnexpectedServiceError(initial.detail)
                verified_starting_host = (
                    initial.state is HealthState.STARTING
                    and initial.payload is not None
                )
                launch_token = (
                    self._token_store.load()
                    if initial.state is HealthState.READY or verified_starting_host
                    else None
                )
                should_start = (
                    initial.state is HealthState.ABSENT
                    or (
                        initial.state is HealthState.STARTING
                        and not verified_starting_host
                    )
                    or (
                        verified_starting_host
                        and launch_token is None
                    )
                )
                if should_start:
                    launch_token = self._token_store.rotate()
                    try:
                        process = self._host_starter.start(
                            self._settings.host_command,
                            self._settings.host_cwd,
                            {
                                **host_only_environment,
                                "CONTROLSTUDIO_LAUNCH_TOKEN": launch_token,
                            },
                        )
                    except OSError as exc:
                        raise HostStartError(
                            f"could not start ControlStudio Host: {exc}"
                        ) from exc
                elif launch_token is None:
                    raise HostStartError(
                        "verified Host is running but its launch session token is unavailable"
                    )

                if initial.state is not HealthState.READY:
                    self._wait_until_ready(process)

                self._verify_launch_token_until_ready(launch_token, process)
                self._verify_elevated_host_retention()

                if open_ui:
                    self._open_ui(
                        launch_token,
                        reuse_existing=(
                            process is None
                            and (
                                initial.state is HealthState.READY
                                or verified_starting_host
                            )
                        ),
                    )
                launch_succeeded = True
            except BaseException:
                self._elevation_receipts.finish(
                    observation,
                    elevation_relaunch_requested=False,
                    host_ready=False,
                    elevated_host_retention_verified=False,
                    outcome="failed",
                )
                raise
            else:
                self._elevation_receipts.finish(
                    observation,
                    elevation_relaunch_requested=False,
                    host_ready=True,
                    elevated_host_retention_verified=True,
                    outcome="ready",
                )
            finally:
                if process is not None:
                    if launch_succeeded:
                        release_process_started_by_launcher(process)
                    else:
                        stop_process_started_by_launcher(process)

    def _verify_elevated_host_retention(self) -> None:
        """Re-check the live listener after readiness without requesting UAC.

        On Windows this asks the broker to compare the running Host token to
        the XG5000-required integrity level. A compliant verified host returns
        false; any inadequate/ambiguous listener fails closed before a receipt
        can describe it as retained.
        """

        if self._elevation_broker.relaunch_if_required(
            verified_host=True,
            parent_opens_ui=False,
        ):
            raise UnexpectedServiceError(
                "ControlStudio unexpectedly requested elevation while verifying a ready Host"
            )

    def _open_elevation_handoff_under_instance_guard(
        self,
        launch_token: str,
    ) -> None:
        """Serialize parent-side UI handoff after the elevated child exits."""

        deadline = self._monotonic() + self._settings.startup_timeout_seconds
        while self._monotonic() < deadline:
            try:
                with self._instance_guard:
                    self._open_ui(launch_token, reuse_existing=False)
                    return
            except LauncherAlreadyRunning:
                self._sleep(self._settings.poll_interval_seconds)
        raise HostStartError(
            "ControlStudio Host became ready but the single-window handoff "
            "remained busy"
        )

    def _open_ui(self, launch_token: str, *, reuse_existing: bool) -> None:
        """Focus a live Host's verified window or create one fresh handoff."""

        if reuse_existing and self._ui_opener.focus_existing():
            return

        handoff = self._health_probe.issue_browser_handoff(launch_token)
        if (
            not handoff.issued
            or handoff.ticket is None
            or handoff.readiness_receipt is None
        ):
            raise UnexpectedServiceError(handoff.detail)
        self._ui_opener.open(
            f"{CANONICAL_BASE_URL}/#handoff_ticket={quote(handoff.ticket, safe='')}"
        )
        self._wait_for_browser_bootstrap(launch_token, handoff.readiness_receipt)

    def _wait_for_browser_bootstrap(
        self,
        launch_token: str,
        readiness_receipt: str,
    ) -> None:
        """Verify the Host-observed bootstrap, not Chromium's visual surface."""

        deadline = self._monotonic() + self._settings.startup_timeout_seconds
        last_detail = "browser bootstrap has not answered yet"
        while self._monotonic() < deadline:
            readiness = self._health_probe.probe_browser_readiness(
                launch_token,
                readiness_receipt,
            )
            if readiness.ready:
                return
            if not readiness.retryable:
                raise UnexpectedServiceError(readiness.detail)
            last_detail = readiness.detail
            self._sleep(self._settings.poll_interval_seconds)
        write_launcher_diagnostic(
            "browser bootstrap readiness timeout "
            f"seconds={self._settings.startup_timeout_seconds:g} detail={last_detail!r}"
        )
        raise HostStartError(
            "ControlStudio browser did not complete its authenticated bootstrap within "
            f"{self._settings.startup_timeout_seconds:g} seconds ({last_detail})"
        )

    def _wait_until_ready(self, process: HostProcess | None) -> None:
        deadline = self._monotonic() + self._settings.startup_timeout_seconds
        last_result = HealthResult(HealthState.ABSENT, "Host has not answered yet")
        last_logged_state: tuple[HealthState, str] | None = None

        while self._monotonic() < deadline:
            if process is not None:
                return_code = process.poll()
                if return_code is not None:
                    raise HostStartError(
                        f"ControlStudio Host exited before readiness (exit code {return_code})"
                    )

            last_result = self._health_probe.probe()
            current_logged_state = (last_result.state, last_result.detail)
            if current_logged_state != last_logged_state:
                write_launcher_diagnostic(
                    f"health transition state={last_result.state.value} "
                    f"detail={last_result.detail!r}"
                )
                last_logged_state = current_logged_state
            if last_result.state is HealthState.READY:
                return
            if last_result.state is HealthState.UNEXPECTED:
                raise UnexpectedServiceError(last_result.detail)
            self._sleep(self._settings.poll_interval_seconds)

        write_launcher_diagnostic(
            f"health timeout seconds={self._settings.startup_timeout_seconds:g} "
            f"last_state={last_result.state.value} detail={last_result.detail!r}"
        )
        raise HostStartError(
            "ControlStudio Host did not become ready within "
            f"{self._settings.startup_timeout_seconds:g} seconds "
            f"(last state: {last_result.detail})"
        )

    def _verify_launch_token_until_ready(
        self,
        launch_token: str,
        process: HostProcess | None,
    ) -> None:
        """Retry only transient transport loss while retaining exact Host ownership."""

        deadline = self._monotonic() + self._settings.startup_timeout_seconds
        last_detail = "launch challenge has not answered yet"
        while self._monotonic() < deadline:
            if process is not None:
                return_code = process.poll()
                if return_code is not None:
                    raise HostStartError(
                        "ControlStudio Host exited before launch challenge "
                        f"verification (exit code {return_code})"
                    )

            verification = self._health_probe.verify_launch_token(launch_token)
            if verification.verified:
                return
            if not verification.retryable:
                raise UnexpectedServiceError(verification.detail)

            last_detail = verification.detail
            write_launcher_diagnostic(
                f"launch challenge transient failure detail={last_detail!r}"
            )
            self._sleep(self._settings.poll_interval_seconds)

        write_launcher_diagnostic(
            "launch challenge timeout "
            f"seconds={self._settings.startup_timeout_seconds:g} "
            f"detail={last_detail!r}"
        )
        raise UnexpectedServiceError(last_detail)
