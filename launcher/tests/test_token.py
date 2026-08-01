from __future__ import annotations

from controlstudio_launcher.token import FileLaunchTokenStore


def test_launch_token_is_rotated_and_reloaded(tmp_path) -> None:
    path = tmp_path / "session.token"
    store = FileLaunchTokenStore(path)

    first = store.rotate()
    second = store.rotate()

    assert len(first) >= 32
    assert len(second) >= 32
    assert first != second
    assert store.load() == second
    assert path.read_text(encoding="utf-8") == second


def test_invalid_or_missing_launch_token_is_not_loaded(tmp_path) -> None:
    path = tmp_path / "session.token"
    store = FileLaunchTokenStore(path)

    assert store.load() is None
    path.write_text("too-short", encoding="utf-8")
    assert store.load() is None
