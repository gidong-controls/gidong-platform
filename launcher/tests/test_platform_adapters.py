from __future__ import annotations

import io
import os
import threading
from pathlib import Path
from uuid import uuid4

import pytest

from controlstudio_launcher.errors import LauncherAlreadyRunning
from controlstudio_launcher.host import (
    SubprocessHostStarter,
    WindowsJobHostProcess,
    release_process_started_by_launcher,
    stop_process_started_by_launcher,
)
from controlstudio_launcher.instance import WindowsNamedMutex, create_instance_guard


class FakeProcess:
    def __init__(self, *, process_handle: int = 123, return_code: int | None = None) -> None:
        self._handle = process_handle
        self.pid = 321
        self.return_code = return_code
        self.terminate_calls = 0
        self.wait_calls: list[float | None] = []

    def poll(self) -> int | None:
        return self.return_code

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.return_code = 1

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        return self.return_code or 0


def test_host_starter_uses_canonical_endpoint_without_a_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    root_process = FakeProcess()
    assigned: list[tuple[int, int]] = []
    closed: list[int] = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return root_process

    monkeypatch.setattr("controlstudio_launcher.host.subprocess.Popen", fake_popen)
    monkeypatch.setenv("CONTROLSTUDIO_BIND_HOST", "::1")
    monkeypatch.setenv("CONTROLSTUDIO_PORT", "9999")
    monkeypatch.setenv("CONTROLSTUDIO_CANONICAL_ORIGIN", "http://127.0.0.1:9999")
    if os.name == "nt":
        monkeypatch.setattr(
            "controlstudio_launcher.host._open_startup_log",
            lambda: io.BytesIO(),
        )
        monkeypatch.setattr(
            "controlstudio_launcher.host._create_windows_job",
            lambda: (456, r"Local\ControlStudio.Launcher.test"),
        )
        monkeypatch.setattr(
            "controlstudio_launcher.host._assign_process_to_windows_job",
            lambda job_handle, process_handle: assigned.append((job_handle, process_handle)),
        )
        monkeypatch.setattr(
            "controlstudio_launcher.host._close_windows_handle",
            lambda handle: closed.append(handle),
        )

    process = SubprocessHostStarter().start(
        ("host.exe", "--bind", "127.0.0.1"),
        Path("C:/ControlStudio"),
        {"CONTROLSTUDIO_LAUNCH_TOKEN": "secret-token"},
    )

    if os.name == "nt":
        assert isinstance(process, WindowsJobHostProcess)
        assert assigned == [(456, 123)]
        release_process_started_by_launcher(process)
        assert closed == [456]
        assert root_process.terminate_calls == 0
    else:
        assert process is root_process
    assert calls[0][0] == ("host.exe", "--bind", "127.0.0.1")
    kwargs = calls[0][1]
    assert kwargs["cwd"] == Path("C:/ControlStudio")
    assert kwargs["shell"] is False
    assert kwargs["close_fds"] is True
    assert kwargs["creationflags"] == (
        __import__("subprocess").CREATE_NO_WINDOW if os.name == "nt" else 0
    )
    assert kwargs["env"]["CONTROLSTUDIO_LAUNCH_TOKEN"] == "secret-token"
    assert kwargs["env"]["CONTROLSTUDIO_BIND_HOST"] == "127.0.0.1"
    assert kwargs["env"]["CONTROLSTUDIO_PORT"] == "8765"
    assert kwargs["env"]["CONTROLSTUDIO_CANONICAL_ORIGIN"] == "http://localhost:8765"
    if os.name == "nt":
        assert (
            kwargs["env"]["CONTROLSTUDIO_LAUNCH_JOB_NAME"]
            == r"Local\ControlStudio.Launcher.test"
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object cleanup")
def test_windows_job_cleanup_terminates_descendants_even_if_root_already_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_process = FakeProcess(return_code=7)
    terminated_jobs: list[int] = []
    closed_jobs: list[int] = []
    monkeypatch.setattr(
        "controlstudio_launcher.host._terminate_windows_job",
        lambda job_handle: terminated_jobs.append(job_handle),
    )
    monkeypatch.setattr(
        "controlstudio_launcher.host._close_windows_handle",
        lambda job_handle: closed_jobs.append(job_handle),
    )
    process = WindowsJobHostProcess(root_process, 456)

    stop_process_started_by_launcher(process)

    assert terminated_jobs == [456]
    assert closed_jobs == [456]
    assert root_process.terminate_calls == 0
    assert root_process.wait_calls == []


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object process tracking")
def test_windows_job_poll_tracks_real_child_after_venv_redirector_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_process = FakeProcess(return_code=0)
    active = [True, False]
    monkeypatch.setattr(
        "controlstudio_launcher.host._windows_job_has_active_processes",
        lambda _job_handle: active.pop(0),
    )
    process = WindowsJobHostProcess(root_process, 456)

    assert process.poll() is None
    assert process.poll() is None


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object process tracking")
def test_windows_job_poll_reports_nonzero_root_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_process = FakeProcess(return_code=7)
    monkeypatch.setattr(
        "controlstudio_launcher.host._windows_job_has_active_processes",
        lambda _job_handle: False,
    )

    assert WindowsJobHostProcess(root_process, 456).poll() == 7


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object assignment")
def test_windows_job_assignment_failure_stops_root_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_process = FakeProcess()
    closed_jobs: list[int] = []
    monkeypatch.setattr(
        "controlstudio_launcher.host.subprocess.Popen",
        lambda *_args, **_kwargs: root_process,
    )
    monkeypatch.setattr(
        "controlstudio_launcher.host._open_startup_log",
        lambda: io.BytesIO(),
    )
    monkeypatch.setattr(
        "controlstudio_launcher.host._create_windows_job",
        lambda: (456, r"Local\ControlStudio.Launcher.test"),
    )
    monkeypatch.setattr(
        "controlstudio_launcher.host._assign_process_to_windows_job",
        lambda *_args: (_ for _ in ()).throw(OSError("assignment failed")),
    )
    monkeypatch.setattr(
        "controlstudio_launcher.host._close_windows_handle",
        lambda job_handle: closed_jobs.append(job_handle),
    )

    with pytest.raises(OSError, match="assignment failed"):
        SubprocessHostStarter().start(("host.exe",), None, {})

    assert root_process.terminate_calls == 1
    assert root_process.wait_calls == [5]
    assert closed_jobs == [456]


def test_guard_factory_is_platform_specific() -> None:
    guard = create_instance_guard()

    if os.name == "nt":
        assert isinstance(guard, WindowsNamedMutex)
    else:
        assert not isinstance(guard, WindowsNamedMutex)


@pytest.mark.skipif(os.name != "nt", reason="Windows named mutex integration")
def test_windows_named_mutex_rejects_a_second_launcher_thread() -> None:
    name = rf"Local\ControlStudio.Launcher.Test.{uuid4()}"
    result: list[type[BaseException] | None] = []

    def attempt_second_lock() -> None:
        try:
            with WindowsNamedMutex(name):
                result.append(None)
        except BaseException as exc:  # capture the cross-thread test result
            result.append(type(exc))

    with WindowsNamedMutex(name):
        thread = threading.Thread(target=attempt_second_lock)
        thread.start()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert result == [LauncherAlreadyRunning]
