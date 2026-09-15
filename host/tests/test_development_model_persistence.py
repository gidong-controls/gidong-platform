from datetime import datetime, timezone

from controlstudio_host.app import HostStore, ModelConnectionRepository, create_app
from controlstudio_host.config import HostConfig


def test_development_defaults_do_not_replace_verified_user_endpoint(tmp_path):
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        development_model_base_url="http://127.0.0.1:9001/v1",
        development_model_name="development-default",
        development_model_api_key="test-only",
    )
    store = HostStore(config.database_path)
    repository = ModelConnectionRepository(store)
    verified = datetime.now(timezone.utc)
    repository.save(
        base_url="http://127.0.0.1:9002/v1", model="user-selected",
        timeout_seconds=300, api_key=None, custom_headers={},
        capability_verified_at=verified, inherit_missing_secrets=False,
        allow_insecure_http=True,
    )
    saved_receipt = repository.get().capability_verified_at
    create_app(config, store=store, model_repository=repository)
    restored = repository.get()
    assert restored.model == "user-selected"
    assert restored.base_url == "http://127.0.0.1:9002/v1"
    assert saved_receipt is not None
    assert restored.capability_verified_at == saved_receipt
    assert restored.secrets.api_key is None
    assert restored.secrets.custom_headers == {}


def test_development_defaults_still_seed_empty_installation(tmp_path):
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        development_model_base_url="http://127.0.0.1:9001/v1",
        development_model_name="development-default",
        development_model_api_key="test-only",
    )
    store = HostStore(config.database_path)
    repository = ModelConnectionRepository(store)
    create_app(config, store=store, model_repository=repository)
    assert repository.get().model == "development-default"
