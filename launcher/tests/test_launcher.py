from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from controlstudio_launcher.app import ControlStudioLauncher
from controlstudio_launcher.config import LauncherSettings
from controlstudio_launcher.errors import (
    HostStartError,
    LauncherAlreadyRunning,
    UiOpenError,
    UnexpectedServiceError,
)
from controlstudio_launcher.health import (
    BrowserHandoffResult,
    HealthResult,
    HealthState,
    LaunchVerificationResult,
)


@pytest.fixture(autouse=True)
def disable_launcher_diagnostic_log(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "controlstudio_launcher.app.write_launcher_diagnostic",
        lambda _message: None,
    )


class Guard:
    entered = False
    exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *args):
        self.exited = True


class ContendedGuard(Guard):
    def __init__(self, failed_entries: int) -> None:
        self.failed_entries = failed_entries
        self.attempts = 0
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.attempts += 1
        if self.attempts <= self.failed_entries:
            raise LauncherAlreadyRunning("elevated child still owns launcher mutex")
        return super().__enter__()


class Probe:
    def __init__(
        self,
        *states: HealthState,
        verified: bool = True,
        verification_results: list[LaunchVerificationResult] | None = None,
        handoff_issued: bool = True,
        starting_payload: dict[str, str] | None = None,
    ) -> None:
        self.states = list(states)
        self.calls = 0
        self.verified = verified
        self.verification_results = list(verification_results or ())
        self.handoff_issued = handoff_issued
        self.starting_payload = starting_payload
        self.challenge_tokens: list[str] = []
        self.handoff_tokens: list[str] = []

    def probe(self) -> HealthResult:
        index = min(self.calls, len(self.states) - 1)
        self.calls += 1
        state = self.states[index]
        payload = self.starting_payload if state is HealthState.STARTING else None
        return HealthResult(state, f"state={state.value}", payload)

    def verify_launch_token(self, launch_token: str) -> LaunchVerificationResult:
        self.challenge_tokens.append(launch_token)
        if self.verification_results:
            return self.verification_results.pop(0)
        return LaunchVerificationResult(
            self.verified,
            "verified" if self.verified else "proof mismatch",
        )

    def issue_browser_handoff(self, launch_token: str) -> BrowserHandoffResult:
        self.handoff_tokens.append(launch_token)
        return BrowserHandoffResult(
            self.handoff_issued,
            "issued" if self.handoff_issued else "handoff refused",
            "browser-handoff-ticket-that-is-long-enough"
            if self.handoff_issued
            else None,
        )


@dataclass
class Process:
    return_codes: list[int | None] = field(default_factory=lambda: [None])
    poll_calls: int = 0
    terminated: bool = False
    waited: bool = False

    def poll(self) -> int | None:
        index = min(self.poll_calls, len(self.return_codes) - 1)
        self.poll_calls += 1
        return self.return_codes[index]

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None) -> int:
        self.waited = True
        return 0


class Starter:
    def __init__(self, process: Process | None = None) -> None:
        self.process = process or Process()
        self.calls: list[tuple[tuple[str, ...], object, dict[str, str]]] = []

    def start(self, command, cwd, environment):
        self.calls.append((command, cwd, dict(environment)))
        return self.process


class Opener:
    def __init__(
        self,
        error: UiOpenError | None = None,
        *,
        existing_window: bool = False,
    ) -> None:
        self.urls: list[str] = []
        self.error = error
        self.existing_window = existing_window
        self.focus_calls = 0

    def focus_existing(self) -> bool:
        self.focus_calls += 1
        return self.existing_window

    def open(self, url: str) -> None:
        self.urls.append(url)
        if self.error is not None:
            raise self.error


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class TokenStore:
    def __init__(
        self,
        token: str | None = "launcher-test-token",
        rotated_token: str = "launcher-test-token",
    ) -> None:
        self.token = token
        self.rotated_token = rotated_token
        self.loads = 0
        self.rotations = 0

    def load(self) -> str | None:
        self.loads += 1
        return self.token

    def rotate(self) -> str:
        self.rotations += 1
        self.token = self.rotated_token
        return self.rotated_token


class ElevationBroker:
    def __init__(self, *, relaunched: bool = False) -> None:
        self.relaunched = relaunched
        self.calls = 0
        self.arguments: list[tuple[bool, bool]] = []

    def relaunch_if_required(
        self,
        *,
        verified_host: bool,
        parent_opens_ui: bool,
    ) -> bool:
        self.calls += 1
        self.arguments.append((verified_host, parent_opens_ui))
        return self.relaunched


def settings(timeout: float = 1.0) -> LauncherSettings:
    return LauncherSettings(
        host_command=("host-test", "--foreground"),
        host_cwd=None,
        expected_host_build="build-1",
        startup_timeout_seconds=timeout,
        probe_timeout_seconds=0.1,
        poll_interval_seconds=0.1,
    )


def make_launcher(
    probe: Probe,
    *,
    starter: Starter | None = None,
    opener: Opener | None = None,
    clock: Clock | None = None,
    token_store: TokenStore | None = None,
    elevation_broker: ElevationBroker | None = None,
):
    guard = Guard()
    starter = starter or Starter()
    opener = opener or Opener()
    clock = clock or Clock()
    token_store = token_store or TokenStore()
    launcher = ControlStudioLauncher(
        settings=settings(),
        health_probe=probe,
        host_starter=starter,
        ui_opener=opener,
        instance_guard=guard,
        token_store=token_store,
        elevation_broker=elevation_broker,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    return launcher, starter, opener, guard


def test_existing_verified_host_is_opened_without_start() -> None:
    launcher, starter, opener, guard = make_launcher(Probe(HealthState.READY))

    launcher.run()

    assert starter.calls == []
    assert opener.urls == [
        "http://127.0.0.1:8765/#handoff_ticket=browser-handoff-ticket-that-is-long-enough"
    ]
    assert guard.entered and guard.exited


def test_existing_verified_host_focuses_owned_window_without_new_handoff() -> None:
    probe = Probe(HealthState.READY)
    opener = Opener(existing_window=True)
    launcher, starter, _, guard = make_launcher(probe, opener=opener)

    launcher.run()

    assert starter.calls == []
    assert opener.focus_calls == 1
    assert opener.urls == []
    assert probe.handoff_tokens == []
    assert guard.entered and guard.exited


def test_absent_host_is_started_once_then_opened() -> None:
    launcher, starter, opener, _ = make_launcher(
        Probe(
            HealthState.ABSENT,
            HealthState.ABSENT,
            HealthState.ABSENT,
            HealthState.STARTING,
            HealthState.READY,
        )
    )

    launcher.run()

    assert starter.calls == [
        (
            ("host-test", "--foreground"),
            None,
            {"CONTROLSTUDIO_LAUNCH_TOKEN": "launcher-test-token"},
        )
    ]
    assert opener.urls == [
        "http://127.0.0.1:8765/#handoff_ticket=browser-handoff-ticket-that-is-long-enough"
    ]


def test_new_host_replaces_stale_window_instead_of_reusing_its_session() -> None:
    opener = Opener(existing_window=True)
    probe = Probe(
        HealthState.ABSENT,
        HealthState.ABSENT,
        HealthState.READY,
    )
    launcher, starter, _, _ = make_launcher(probe, opener=opener)

    launcher.run()

    assert starter.calls
    assert opener.focus_calls == 0
    assert opener.urls == [
        "http://127.0.0.1:8765/#handoff_ticket=browser-handoff-ticket-that-is-long-enough"
    ]
    assert probe.handoff_tokens == ["launcher-test-token"]


def test_elevated_relaunch_hands_off_without_starting_a_second_host() -> None:
    elevation_broker = ElevationBroker(relaunched=True)
    launcher, starter, opener, guard = make_launcher(
        Probe(HealthState.ABSENT, HealthState.ABSENT, HealthState.READY),
        elevation_broker=elevation_broker,
    )

    launcher.run()

    assert elevation_broker.calls == 1
    assert elevation_broker.arguments == [(False, True)]
    assert starter.calls == []
    assert opener.urls == [
        "http://127.0.0.1:8765/#handoff_ticket=browser-handoff-ticket-that-is-long-enough"
    ]
    assert guard.entered and guard.exited


def test_network_timeout_hands_off_elevation_before_instance_lock() -> None:
    elevation_broker = ElevationBroker(relaunched=True)
    launcher, starter, opener, guard = make_launcher(
        Probe(HealthState.STARTING, HealthState.STARTING, HealthState.READY),
        elevation_broker=elevation_broker,
    )

    launcher.run()

    assert elevation_broker.calls == 1
    assert elevation_broker.arguments == [(False, True)]
    assert starter.calls == []
    assert opener.urls == [
        "http://127.0.0.1:8765/#handoff_ticket=browser-handoff-ticket-that-is-long-enough"
    ]
    assert guard.entered and guard.exited


def test_elevation_parent_waits_for_child_mutex_before_opening_ui() -> None:
    elevation_broker = ElevationBroker(relaunched=True)
    probe = Probe(HealthState.ABSENT, HealthState.READY)
    opener = Opener()
    clock = Clock()
    guard = ContendedGuard(failed_entries=2)
    launcher = ControlStudioLauncher(
        settings=settings(),
        health_probe=probe,
        host_starter=Starter(),
        ui_opener=opener,
        instance_guard=guard,
        token_store=TokenStore(),
        elevation_broker=elevation_broker,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    launcher.run()

    assert guard.attempts == 3
    assert guard.entered and guard.exited
    assert clock.now == pytest.approx(0.2)
    assert len(opener.urls) == 1


def test_no_open_elevation_handoff_still_waits_and_verifies_host() -> None:
    elevation_broker = ElevationBroker(relaunched=True)
    probe = Probe(
        HealthState.ABSENT,
        HealthState.STARTING,
        HealthState.READY,
    )
    launcher, starter, opener, guard = make_launcher(
        probe,
        elevation_broker=elevation_broker,
    )

    launcher.run(open_ui=False)

    assert elevation_broker.arguments == [(False, False)]
    assert starter.calls == []
    assert opener.urls == []
    assert probe.challenge_tokens == ["launcher-test-token"]
    assert not guard.entered


def test_no_open_elevation_handoff_reports_missing_host() -> None:
    elevation_broker = ElevationBroker(relaunched=True)
    launcher, starter, opener, guard = make_launcher(
        Probe(HealthState.ABSENT),
        elevation_broker=elevation_broker,
    )

    with pytest.raises(HostStartError, match="did not become ready"):
        launcher.run(open_ui=False)

    assert starter.calls == []
    assert opener.urls == []
    assert not guard.entered


def test_existing_host_does_not_request_elevation_again() -> None:
    elevation_broker = ElevationBroker(relaunched=False)
    launcher, starter, opener, _ = make_launcher(
        Probe(HealthState.READY, HealthState.READY),
        elevation_broker=elevation_broker,
    )

    launcher.run()

    assert elevation_broker.calls == 1
    assert elevation_broker.arguments == [(True, True)]
    assert starter.calls == []
    assert opener.urls


def test_existing_starting_host_is_waited_for_without_duplicate_start() -> None:
    launcher, starter, opener, _ = make_launcher(
        Probe(
            HealthState.STARTING,
            HealthState.STARTING,
            HealthState.STARTING,
            HealthState.READY,
            starting_payload={"service": "controlstudio-host"},
        )
    )

    launcher.run()

    assert starter.calls == []
    assert opener.urls == [
        "http://127.0.0.1:8765/#handoff_ticket=browser-handoff-ticket-that-is-long-enough"
    ]


def test_timeout_with_no_existing_token_starts_host_instead_of_waiting_forever() -> None:
    token_store = TokenStore(token=None, rotated_token="new-launch-token")
    launcher, starter, opener, _ = make_launcher(
        Probe(HealthState.STARTING, HealthState.STARTING, HealthState.READY),
        token_store=token_store,
    )

    launcher.run()

    assert starter.calls == [
        (
            ("host-test", "--foreground"),
            None,
            {"CONTROLSTUDIO_LAUNCH_TOKEN": "new-launch-token"},
        )
    ]
    assert opener.urls == [
        "http://127.0.0.1:8765/#handoff_ticket=browser-handoff-ticket-that-is-long-enough"
    ]


def test_unexpected_service_is_rejected_without_start_or_open() -> None:
    launcher, starter, opener, _ = make_launcher(Probe(HealthState.UNEXPECTED))

    with pytest.raises(UnexpectedServiceError):
        launcher.run()

    assert starter.calls == []
    assert opener.urls == []


def test_unexpected_service_after_start_terminates_only_started_process() -> None:
    process = Process()
    starter = Starter(process)
    launcher, _, opener, _ = make_launcher(
        Probe(HealthState.ABSENT, HealthState.ABSENT, HealthState.UNEXPECTED),
        starter=starter,
    )

    with pytest.raises(UnexpectedServiceError):
        launcher.run()

    assert process.terminated
    assert process.waited
    assert opener.urls == []


def test_wrong_service_challenge_is_rejected_before_secret_reaches_ui() -> None:
    process = Process()
    starter = Starter(process)
    probe = Probe(HealthState.ABSENT, HealthState.ABSENT, HealthState.READY, verified=False)
    launcher, _, opener, _ = make_launcher(probe, starter=starter)

    with pytest.raises(UnexpectedServiceError, match="proof mismatch"):
        launcher.run()

    assert process.terminated
    assert opener.urls == []
    assert probe.challenge_tokens == ["launcher-test-token"]


def test_transient_launch_challenge_timeout_retries_same_started_host() -> None:
    process = Process()
    starter = Starter(process)
    probe = Probe(
        HealthState.ABSENT,
        HealthState.ABSENT,
        HealthState.READY,
        verification_results=[
            LaunchVerificationResult(
                False,
                "launch challenge could not be verified: timed out",
                retryable=True,
            ),
            LaunchVerificationResult(True, "verified Host launch challenge"),
        ],
    )
    clock = Clock()
    guard = Guard()
    opener = Opener()
    launcher = ControlStudioLauncher(
        settings=settings(),
        health_probe=probe,
        host_starter=starter,
        ui_opener=opener,
        instance_guard=guard,
        token_store=TokenStore(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    launcher.run()

    assert len(starter.calls) == 1
    assert probe.challenge_tokens == ["launcher-test-token"] * 2
    assert not process.terminated
    assert opener.urls == [
        "http://127.0.0.1:8765/#handoff_ticket=browser-handoff-ticket-that-is-long-enough"
    ]
    assert clock.now == pytest.approx(0.1)


def test_early_host_exit_is_reported() -> None:
    starter = Starter(Process(return_codes=[7]))
    launcher, _, _, _ = make_launcher(
        Probe(HealthState.ABSENT, HealthState.ABSENT, HealthState.ABSENT),
        starter=starter,
    )

    with pytest.raises(HostStartError, match="exit code 7"):
        launcher.run()


def test_timeout_stops_process_started_by_this_launcher() -> None:
    process = Process()
    starter = Starter(process)
    clock = Clock()
    probe = Probe(HealthState.ABSENT)
    guard = Guard()
    opener = Opener()
    launcher = ControlStudioLauncher(
        settings=settings(timeout=0.25),
        health_probe=probe,
        host_starter=starter,
        ui_opener=opener,
        instance_guard=guard,
        token_store=TokenStore(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    with pytest.raises(HostStartError, match="did not become ready"):
        launcher.run()

    assert process.terminated
    assert opener.urls == []


def test_ui_failure_stops_only_the_host_started_by_this_launcher() -> None:
    process = Process()
    starter = Starter(process)
    opener = Opener(UiOpenError("browser unavailable"))
    launcher, _, _, _ = make_launcher(
        Probe(HealthState.ABSENT, HealthState.ABSENT, HealthState.READY),
        starter=starter,
        opener=opener,
    )

    with pytest.raises(UiOpenError, match="browser unavailable"):
        launcher.run()

    assert process.terminated
    assert process.waited


def test_no_open_mode_still_verifies_host() -> None:
    launcher, starter, opener, _ = make_launcher(
        Probe(HealthState.READY, HealthState.READY)
    )

    launcher.run(open_ui=False)

    assert starter.calls == []
    assert opener.urls == []
