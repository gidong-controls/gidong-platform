import pytest

from controlstudio_host.config import HostConfig


@pytest.mark.parametrize("value", [
    "http://localhost:8767@example.invalid", "http://localhost:notaport/a",
    "http://localhost:0", "http://localhost:65536", "http://127.0.0.1:8767/path",
    "http://localhost:8767?query", "http://localhost:8767#fragment",
    "http://localhost:8767\n", "http://localhost:8767@127.0.0.1:8767",
    "http://example.invalid:8767", "https://localhost:8767",
])
def test_explicit_origin_rejects_non_origins(value):
    with pytest.raises(ValueError, match="canonical_origin"):
        HostConfig(canonical_origin=value)


@pytest.mark.parametrize("value,expected", [
    (None, "http://localhost:8767"), ("http://localhost:5173/", "http://localhost:5173"),
    ("http://127.0.0.1:8767", "http://127.0.0.1:8767"),
    ("http://localhost:80", "http://localhost"),
    ("http://localhost:08767", "http://localhost:8767"),
])
def test_origin_is_serialized_like_browser_origin(value, expected):
    assert HostConfig(port=8767, canonical_origin=value).canonical_origin == expected
