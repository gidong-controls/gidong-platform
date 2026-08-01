from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path

import pytest

from controlstudio_host.model_connection import (
    MemorySecretStore,
    ModelConnectionRepository,
    ModelSettingsError,
    WindowsCredentialSecretStore,
    is_external_model_endpoint,
    validate_model_base_url,
)
from controlstudio_host.store import HostStore

FIRST_TEST_API_KEY_VALUE = "first-secret"
SECOND_TEST_API_KEY_VALUE = "second-secret"
PROVIDER_TEST_API_KEY_VALUE = "provider-a-secret"
INHERITANCE_TEST_API_KEY_VALUE = "must-not-be-inherited"


@pytest.mark.skipif(os.name != "nt", reason="Windows Credential Manager is Windows-only")
def test_windows_credential_secret_store_roundtrip() -> None:
    store = WindowsCredentialSecretStore()
    reference = f"ControlStudio/test/{uuid.uuid4()}"
    payload = b'{"api_key":"dummy","custom_headers":{},"schema_version":1}'
    try:
        store.put(reference, payload)
        assert store.get(reference) == payload
    finally:
        store.delete(reference)


def test_model_credentials_are_outside_sqlite_and_can_be_rotated(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "host.sqlite3"
    store = HostStore(database_path)
    secrets = MemorySecretStore()
    repository = ModelConnectionRepository(store, secrets)

    first = repository.save(
        base_url="http://127.0.0.1:4000/v1/",
        model="controlstudio-primary",
        timeout_seconds=45,
        api_key=FIRST_TEST_API_KEY_VALUE,
        custom_headers={"X-Gateway": "private-header"},
    )
    assert first.base_url == "http://127.0.0.1:4000/v1"
    assert first.secrets.api_key == "first-secret"
    assert first.secrets.custom_headers == {"X-Gateway": "private-header"}

    with sqlite3.connect(database_path) as connection:
        serialized_rows = "\n".join(
            str(row)
            for table in ("model_settings",)
            for row in connection.execute(f"SELECT * FROM {table}")
        )
    assert "first-secret" not in serialized_rows
    assert "private-header" not in serialized_rows
    assert first.secret_ref in serialized_rows

    second = repository.save(
        base_url=first.base_url,
        model="controlstudio-primary-v2",
        timeout_seconds=60,
        api_key=SECOND_TEST_API_KEY_VALUE,
        custom_headers={},
    )
    assert first.secret_ref not in secrets.values
    assert second.secret_ref in secrets.values
    assert repository.get() == second


def test_non_loopback_http_model_endpoint_is_rejected() -> None:
    with pytest.raises(ModelSettingsError, match="insecure_http_consent"):
        validate_model_base_url("http://models.example/v1")

    assert (
        validate_model_base_url(
            "http://100.64.0.10:8080/v1",
            allow_insecure_http=True,
        )
        == "http://100.64.0.10:8080/v1"
    )
    with pytest.raises(ModelSettingsError, match="private or Tailscale IP"):
        validate_model_base_url(
            "http://models.example/v1",
            allow_insecure_http=True,
        )
    for disallowed_address in (
        "8.8.8.8",
        "192.0.2.1",
        "198.51.100.1",
        "203.0.113.1",
        "0.0.0.0",
        "240.0.0.1",
        "169.254.1.1",
        "2001:db8::1",
        "fe80::1",
    ):
        with pytest.raises(ModelSettingsError, match="private or Tailscale"):
            validate_model_base_url(
                f"http://[{disallowed_address}]/v1"
                if ":" in disallowed_address
                else f"http://{disallowed_address}/v1",
                allow_insecure_http=True,
            )
    for allowed_address in (
        "10.0.0.1",
        "172.16.0.1",
        "192.168.0.1",
        "100.64.0.10",
        "fc00::1",
    ):
        assert validate_model_base_url(
            f"http://[{allowed_address}]/v1"
            if ":" in allowed_address
            else f"http://{allowed_address}/v1",
            allow_insecure_http=True,
        )
    assert validate_model_base_url("https://models.example/v1/") == ("https://models.example/v1")
    assert validate_model_base_url("http://localhost:4000/v1") == ("http://localhost:4000/v1")
    assert is_external_model_endpoint("https://models.example/v1") is True
    assert is_external_model_endpoint("http://127.0.0.1:4000/v1") is False


def test_secrets_are_preserved_only_for_the_exact_same_endpoint(tmp_path: Path) -> None:
    repository = ModelConnectionRepository(
        HostStore(tmp_path / "host.sqlite3"),
        MemorySecretStore(),
    )
    first = repository.save(
        base_url="https://provider-a.example/v1",
        model="model-a",
        timeout_seconds=30,
        api_key=PROVIDER_TEST_API_KEY_VALUE,
        custom_headers={"X-Tenant": "tenant-a"},
    )

    same_endpoint = repository.save(
        base_url=first.base_url,
        model="model-b",
        timeout_seconds=30,
        api_key=None,
        custom_headers=None,
    )
    assert same_endpoint.secrets.api_key == "provider-a-secret"
    assert same_endpoint.secrets.custom_headers == {"X-Tenant": "tenant-a"}

    changed_endpoint = repository.save(
        base_url="https://provider-b.example/v1",
        model="model-b",
        timeout_seconds=30,
        api_key=None,
        custom_headers=None,
    )
    assert changed_endpoint.secrets.api_key is None
    assert changed_endpoint.secrets.custom_headers == {}


def test_exact_save_does_not_reinherit_a_concurrently_changed_secret(
    tmp_path: Path,
) -> None:
    repository = ModelConnectionRepository(
        HostStore(tmp_path / "host.sqlite3"),
        MemorySecretStore(),
    )
    repository.save(
        base_url="https://provider.example/v1",
        model="model-a",
        timeout_seconds=30,
        api_key=INHERITANCE_TEST_API_KEY_VALUE,
        custom_headers={"X-Tenant": "must-not-be-inherited"},
    )

    exact = repository.save(
        base_url="https://provider.example/v1",
        model="model-b",
        timeout_seconds=30,
        api_key=None,
        custom_headers={},
        inherit_missing_secrets=False,
    )

    assert exact.secrets.api_key is None
    assert exact.secrets.custom_headers == {}
