from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from .store import HostStore

MODEL_PROTOCOL = "openai_chat_completions"
_INSECURE_HTTP_ALLOWED_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "fc00::/7",
    )
)
_BLOCKED_HEADER_NAMES = frozenset(
    {
        "connection",
        "content-length",
        "host",
        "proxy-authorization",
        "proxy-authenticate",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


class ModelSettingsError(ValueError):
    pass


class StoredModelSettingsError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelSecrets:
    api_key: str | None
    custom_headers: dict[str, str]


def redact_known_model_secrets(value: str, secrets: ModelSecrets) -> str:
    """Remove configured credential material from diagnostic text."""

    redacted = value
    candidates = {
        secret
        for secret in (
            secrets.api_key,
            *secrets.custom_headers.values(),
        )
        if isinstance(secret, str) and secret
    }
    for secret in sorted(candidates, key=len, reverse=True):
        redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


@dataclass(frozen=True)
class StoredModelConnection:
    protocol: str
    base_url: str
    model: str
    timeout_seconds: float
    updated_at: datetime
    capability_verified_at: datetime | None
    secrets: ModelSecrets


class ModelConnectionRepository:
    def __init__(self, store: HostStore) -> None:
        self._store = store

    def get(self) -> StoredModelConnection | None:
        record = self._store.get_model_settings_record()
        if record is None:
            return None
        encoded = record.get("secret_payload_json")
        if not isinstance(encoded, str):
            raise StoredModelSettingsError(
                "stored model configuration has no inline credentials"
            )
        secret_payload = self._decode_secret_payload(encoded.encode("utf-8"))
        return StoredModelConnection(
            protocol=str(record["protocol"]),
            base_url=str(record["base_url"]),
            model=str(record["model"]),
            timeout_seconds=float(record["timeout_seconds"]),
            updated_at=datetime.fromisoformat(str(record["updated_at"])),
            capability_verified_at=(
                datetime.fromisoformat(str(record["capability_verified_at"]))
                if record["capability_verified_at"]
                else None
            ),
            secrets=secret_payload,
        )

    def save(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float,
        api_key: str | None,
        custom_headers: dict[str, str] | None,
        capability_verified_at: datetime | None = None,
        inherit_missing_secrets: bool = True,
        allow_insecure_http: bool = False,
    ) -> StoredModelConnection:
        normalized_url = validate_model_base_url(
            base_url,
            allow_insecure_http=allow_insecure_http,
        )
        normalized_model = validate_model_name(model)
        normalized_timeout = validate_timeout(timeout_seconds)
        normalized_headers = (
            validate_custom_headers(custom_headers) if custom_headers is not None else None
        )

        previous = self.get()
        same_endpoint = previous is not None and previous.base_url == normalized_url
        resolved_api_key = (
            previous.secrets.api_key
            if (
                inherit_missing_secrets
                and api_key is None
                and same_endpoint
                and previous is not None
            )
            else api_key
        )
        resolved_headers = (
            previous.secrets.custom_headers
            if (
                inherit_missing_secrets
                and normalized_headers is None
                and same_endpoint
                and previous is not None
            )
            else (normalized_headers or {})
        )
        if resolved_api_key is not None:
            resolved_api_key = validate_secret_text(resolved_api_key, "api_key")

        encoded = self._encode_secret_payload(
            ModelSecrets(
                api_key=resolved_api_key,
                custom_headers=resolved_headers,
            )
        )
        self._store.upsert_model_settings_record(
            protocol=MODEL_PROTOCOL,
            base_url=normalized_url,
            model=normalized_model,
            secret_payload_json=encoded.decode("utf-8"),
            timeout_seconds=normalized_timeout,
            capability_verified_at=capability_verified_at,
        )
        saved = self.get()
        assert saved is not None
        return saved

    @staticmethod
    def _encode_secret_payload(value: ModelSecrets) -> bytes:
        return json.dumps(
            {
                "schema_version": 1,
                "api_key": value.api_key,
                "custom_headers": value.custom_headers,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @staticmethod
    def _decode_secret_payload(value: bytes) -> ModelSecrets:
        try:
            payload = json.loads(value.decode("utf-8"))
            if payload.get("schema_version") != 1:
                raise ValueError("unsupported secret schema")
            api_key = payload.get("api_key")
            headers = payload.get("custom_headers", {})
            if api_key is not None and not isinstance(api_key, str):
                raise TypeError("api_key is not text")
            if not isinstance(headers, dict) or not all(
                isinstance(key, str) and isinstance(item, str) for key, item in headers.items()
            ):
                raise TypeError("custom_headers is not a string map")
            return ModelSecrets(api_key=api_key, custom_headers=headers)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            AttributeError,
            TypeError,
            ValueError,
        ) as exc:
            raise StoredModelSettingsError("stored inline model settings are invalid") from exc


def validate_model_base_url(
    value: str,
    *,
    allow_insecure_http: bool = False,
) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ModelSettingsError("base_url must be a non-empty URL")
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ModelSettingsError("base_url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ModelSettingsError("base_url cannot contain embedded credentials")
    if parsed.query or parsed.fragment:
        raise ModelSettingsError("base_url cannot contain a query or fragment")

    loopback = parsed.hostname.casefold() == "localhost"
    if not loopback:
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = False
    if parsed.scheme != "https" and not loopback:
        if not allow_insecure_http:
            raise ModelSettingsError(
                "non-loopback HTTP model endpoints require explicit insecure_http_consent"
            )
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as exc:
            raise ModelSettingsError(
                "insecure HTTP is allowed only for an explicit private or Tailscale IP address"
            ) from exc
        if not any(address in network for network in _INSECURE_HTTP_ALLOWED_NETWORKS):
            raise ModelSettingsError(
                "insecure HTTP is allowed only for a private or Tailscale IP address"
            )
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def validate_model_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ModelSettingsError("model must be a non-empty name up to 256 characters")
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise ModelSettingsError("model contains an invalid character")
    return value.strip()


def validate_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ModelSettingsError("timeout_seconds must be a number")
    result = float(value)
    if not 1 <= result <= 3600:
        raise ModelSettingsError("timeout_seconds must be between 1 and 3600")
    return result


def validate_secret_text(value: str, field: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ModelSettingsError(f"{field} must be text up to 2048 characters")
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise ModelSettingsError(f"{field} contains an invalid character")
    return value


def validate_custom_headers(value: dict[str, str] | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 16:
        raise ModelSettingsError("custom_headers must contain at most 16 headers")
    normalized: dict[str, str] = {}
    for name, item in value.items():
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 128
            or any(character in name for character in ("\x00", "\r", "\n", ":"))
        ):
            raise ModelSettingsError("custom header name is invalid")
        normalized_name = name.strip()
        if normalized_name.casefold() in _BLOCKED_HEADER_NAMES:
            raise ModelSettingsError(f"custom header is not allowed: {normalized_name}")
        normalized[normalized_name] = validate_secret_text(item, normalized_name)
    return normalized


def is_external_model_endpoint(base_url: str) -> bool:
    hostname = urlsplit(base_url).hostname or ""
    if hostname.casefold() == "localhost":
        return False
    try:
        return not ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return True
