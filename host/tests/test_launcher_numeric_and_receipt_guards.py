"""Launcher configuration and optional diagnostics, using only temporary files."""

import json
from pathlib import Path

import pytest


@pytest.fixture
def launcher_modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "launcher/src"))
    from controlstudio_launcher import config, elevation_receipt
    from controlstudio_launcher.errors import ConfigurationError

    return config, elevation_receipt, ConfigurationError


@pytest.mark.parametrize("value", [0.01, 1, 180.0, 300])
def test_launcher_accepts_existing_positive_finite_range(launcher_modules, value):
    config, _, _ = launcher_modules
    result = config.apply_config(config.default_settings(), {"startup_timeout_seconds": value})
    assert result.startup_timeout_seconds == float(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"),
                                  True, False, 0, -1, 300.01, "1"])
def test_launcher_rejects_nonfinite_and_out_of_range_timeout(launcher_modules, value):
    config, _, error = launcher_modules
    with pytest.raises(error):
        config.apply_config(config.default_settings(), {"startup_timeout_seconds": value})


@pytest.mark.parametrize("stored", [None, [], True, 7, "text", {},
                                    {"schema": "wrong", "observations": []},
                                    {"schema": "controlstudio-launcher-elevation-observation/v1",
                                     "observations": None}])
def test_damaged_receipt_shape_cannot_block_finish(launcher_modules, tmp_path, stored):
    _, receipt, _ = launcher_modules
    path = tmp_path / "observations.json"
    path.write_text(json.dumps(stored), encoding="utf-8")
    store = receipt.ElevationReceiptStore(path)
    observation = store.begin(initial_host_state="ready", initial_host_verified=True, role="original")
    store.finish(observation, elevation_relaunch_requested=False, host_ready=True,
                 elevated_host_retention_verified=True, outcome="ready")
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["schema"] == receipt.SCHEMA
    assert len(result["observations"]) == 1
    assert result["observations"][0]["observation_id"] == observation["observation_id"]
    assert result["observations"][0]["outcome"] == "ready"


def test_valid_receipt_history_is_preserved(launcher_modules, tmp_path):
    _, receipt, _ = launcher_modules
    path = tmp_path / "observations.json"
    original = {"observation_id": "previous", "outcome": "failed"}
    path.write_text(json.dumps({"schema": receipt.SCHEMA, "observations": [original]}), encoding="utf-8")
    receipt.ElevationReceiptStore(path).finish(
        {"observation_id": "new"}, elevation_relaunch_requested=False,
        host_ready=True, elevated_host_retention_verified=True, outcome="ready",
    )
    rows = json.loads(path.read_text(encoding="utf-8"))["observations"]
    assert rows[0] == original
    assert rows[1]["observation_id"] == "new" and rows[1]["outcome"] == "ready"
