from __future__ import annotations

import os

import pytest

from controlstudio_host.launch_job import (
    LAUNCH_JOB_ENVIRONMENT_VARIABLE,
    join_launcher_job_from_environment,
)


def test_direct_host_launch_does_not_require_a_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "controlstudio_host.launch_job._join_windows_job",
        lambda _name: pytest.fail("direct launch must not open a Job"),
    )

    join_launcher_job_from_environment({})


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher Job")
def test_launcher_job_name_is_joined_before_host_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    joined: list[str] = []
    monkeypatch.setattr(
        "controlstudio_host.launch_job._join_windows_job",
        lambda name: joined.append(name),
    )

    join_launcher_job_from_environment(
        {LAUNCH_JOB_ENVIRONMENT_VARIABLE: r"Local\ControlStudio.Launcher.test"}
    )

    assert joined == [r"Local\ControlStudio.Launcher.test"]
