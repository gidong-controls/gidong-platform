from __future__ import annotations

import ipaddress
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from .api_models import ModelContextV1, ReasoningEffortV1
from .credential_store import CredentialStoreError, WindowsDpapiCredentialStore
from .store import HostStore

MODEL_PROTOCOL = "openai_chat_completions"
_REASONING_EFFORT_UNSET = object()
_REASONING_EFFORT_VALUES = frozenset(
    {
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    }
)
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
_CREDENTIAL_OPERATION_ERRORS = (
    CredentialStoreError,
    OSError,
    RuntimeError,
    ValueError,
    TypeError,
    sqlite3.Error,
)
_QUARANTINE_REFERENCE_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class ModelSettingsError(ValueError):
    pass


class StoredModelSettingsError(RuntimeError):
    pass


class CredentialStore(Protocol):
    def put(self, value: bytes) -> str: ...

    def get(self, reference: str) -> bytes: ...

    def delete(self, reference: str) -> None: ...

    def protect_for_quarantine(self, value: bytes) -> bytes: ...

    def unprotect_quarantined(self, value: bytes) -> bytes: ...


@dataclass(frozen=True)
class ModelSecrets:
    # A StoredModelConnection can cross diagnostics/capability-cache paths;
    # accidental repr/logging must not become a plaintext export channel.
    api_key: str | None = field(repr=False)
    custom_headers: dict[str, str] = field(repr=False)


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
    model_context: ModelContextV1 | None = None
    reasoning_effort: ReasoningEffortV1 | None = None


class ModelConnectionRepository:
    def __init__(
        self,
        store: HostStore,
        *,
        credential_store: CredentialStore | None = None,
    ) -> None:
        self._store = store
        self._credential_store = credential_store or WindowsDpapiCredentialStore(
            store.database_path.parent / "credentials"
        )

    def get(self) -> StoredModelConnection | None:
        record = self._store.get_model_settings_record()
        if record is None:
            return None
        stored_reference = record.get("secret_payload_json")
        if not isinstance(stored_reference, str):
            raise StoredModelSettingsError(
                "stored model configuration has no credential reference"
            )
        quarantine_reference = self._quarantine_reference(stored_reference)
        if quarantine_reference is not None:
            secret_payload = self._promote_quarantined_secret(
                stored_reference,
                quarantine_reference,
            )
        else:
            reference = self._credential_reference(stored_reference)
            if reference is None:
                reference, secret_payload = self._migrate_inline_secret(
                    record,
                    stored_reference,
                )
            else:
                secret_payload = None
            if secret_payload is None:
                try:
                    protected_payload = self._credential_store.get(reference)
                except _CREDENTIAL_OPERATION_ERRORS:
                    # Backend paths/details must not cross the Host error surface;
                    # provider failures can accidentally include credential material.
                    raise StoredModelSettingsError(
                        "stored model credentials are unavailable"
                    ) from None
                secret_payload = self._decode_secret_payload(protected_payload)
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
            model_context=_decode_model_context(record.get("model_context_json")),
            reasoning_effort=_decode_reasoning_effort(record.get("reasoning_effort")),
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
        model_context: ModelContextV1 | dict[str, object] | None = None,
        reasoning_effort: ReasoningEffortV1 | None | object = _REASONING_EFFORT_UNSET,
        inherit_missing_secrets: bool = True,
        allow_insecure_http: bool = False,
    ) -> StoredModelConnection:
        normalized_url = validate_model_base_url(
            base_url,
            allow_insecure_http=allow_insecure_http,
        )
        normalized_model = validate_model_name(model)
        normalized_timeout = validate_timeout(timeout_seconds)
        normalized_reasoning_effort = (
            None
            if reasoning_effort is _REASONING_EFFORT_UNSET
            else validate_reasoning_effort(reasoning_effort)
        )
        normalized_headers = (
            validate_custom_headers(custom_headers) if custom_headers is not None else None
        )

        previous_record = self._store.get_model_settings_record()
        same_endpoint = bool(
            previous_record is not None
            and previous_record.get("base_url") == normalized_url
        )
        if reasoning_effort is _REASONING_EFFORT_UNSET and same_endpoint:
            normalized_reasoning_effort = _decode_reasoning_effort(
                previous_record.get("reasoning_effort")
            )
        # Explicit replacement is the recovery path for a legacy setting
        # whose credential backend is unavailable. Only load
        # the previous protected value when an omitted field needs inheritance.
        previous: StoredModelConnection | None = None
        if inherit_missing_secrets and same_endpoint and (
            api_key is None or normalized_headers is None
        ):
            previous = self.get()
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
        previous_reference: str | None = None
        previous_quarantine_reference: str | None = None
        if previous_record is not None:
            try:
                previous_reference = self._credential_reference(
                    previous_record.get("secret_payload_json")
                )
                previous_quarantine_reference = self._quarantine_reference(
                    previous_record.get("secret_payload_json")
                )
            except StoredModelSettingsError:
                # An invalid old reference is not a protected object that
                # needs deletion. Explicit re-entry below can repair it.
                previous_reference = None
                previous_quarantine_reference = None
        try:
            reference = self._credential_store.put(encoded)
            self._store.upsert_model_settings_record(
                protocol=MODEL_PROTOCOL,
                base_url=normalized_url,
                model=normalized_model,
                secret_payload_json=self._encode_credential_reference(reference),
                timeout_seconds=normalized_timeout,
                capability_verified_at=capability_verified_at,
                model_context_json=_encode_model_context(model_context),
                reasoning_effort=normalized_reasoning_effort,
            )
        except _CREDENTIAL_OPERATION_ERRORS:
            if "reference" in locals():
                self._delete_protected_quietly(reference)
            raise StoredModelSettingsError(
                "could not store protected model credentials"
            ) from None
        if previous_reference is not None and previous_reference != reference:
            # The database no longer points at this credential. A protected
            # orphan is safer than rolling the active configuration back to
            # plaintext.
            self._delete_protected_quietly(previous_reference)
        if previous_quarantine_reference is not None:
            self._delete_quarantine_quietly(previous_quarantine_reference)
        saved = self.get()
        assert saved is not None
        return saved

    @staticmethod
    def _encode_credential_reference(reference: str) -> str:
        return json.dumps(
            {"schema_version": 2, "credential_ref": reference},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _credential_reference(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict) or payload.get("schema_version") != 2:
            return None
        reference = payload.get("credential_ref")
        if not isinstance(reference, str) or not reference:
            raise StoredModelSettingsError("stored credential reference is invalid")
        return reference

    @staticmethod
    def _quarantine_reference(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict) or payload.get("schema_version") != 3:
            return None
        reference = payload.get("quarantine_ref")
        if (
            set(payload) != {"schema_version", "quarantine_ref"}
            or not isinstance(reference, str)
            or _QUARANTINE_REFERENCE_PATTERN.fullmatch(reference) is None
        ):
            raise StoredModelSettingsError(
                "stored protected credential reference is invalid"
            )
        return reference

    @staticmethod
    def _encode_quarantine_reference(reference: str) -> str:
        return json.dumps(
            {"quarantine_ref": reference, "schema_version": 3},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def _delete_protected_quietly(self, reference: str) -> None:
        try:
            self._credential_store.delete(reference)
        except _CREDENTIAL_OPERATION_ERRORS:
            return

    def _delete_quarantine_quietly(self, reference: str) -> None:
        try:
            self._store.delete_model_credential_quarantine(reference)
        except _CREDENTIAL_OPERATION_ERRORS:
            return

    def _migrate_inline_secret(
        self,
        record: dict[str, object],
        encoded: str,
    ) -> tuple[str | None, ModelSecrets | None]:
        """Replace a legacy inline payload only after secure persistence succeeds."""

        secrets = self._decode_secret_payload(encoded.encode("utf-8"))
        protected_payload = self._encode_secret_payload(secrets)
        try:
            reference = self._credential_store.put(protected_payload)
        except _CREDENTIAL_OPERATION_ERRORS:
            protector = getattr(self._credential_store, "protect_for_quarantine", None)
            if not callable(protector):
                raise StoredModelSettingsError(
                    "could not securely protect legacy model credentials for recovery; "
                    "the original value remains available for retry"
                ) from None
            try:
                quarantined_payload = protector(protected_payload)
            except _CREDENTIAL_OPERATION_ERRORS:
                raise StoredModelSettingsError(
                    "could not securely protect legacy model credentials for recovery; "
                    "the original value remains available for retry"
                ) from None
            if not isinstance(quarantined_payload, bytes) or not quarantined_payload:
                raise StoredModelSettingsError(
                    "could not securely protect legacy model credentials for recovery; "
                    "the original value remains available for retry"
                ) from None
            quarantine_reference = uuid.uuid4().hex
            try:
                self._store.quarantine_model_credential(
                    reference=quarantine_reference,
                    protected_payload=quarantined_payload,
                    expected_secret_payload_json=encoded,
                    quarantine_payload_json=self._encode_quarantine_reference(
                        quarantine_reference
                    ),
                )
            except _CREDENTIAL_OPERATION_ERRORS:
                raise StoredModelSettingsError(
                    "could not persist protected model credential recovery data; "
                    "the original value remains available for retry"
                ) from None
            raise StoredModelSettingsError(
                "stored model credentials are unavailable; retry migration or re-enter credentials"
            ) from None
        try:
            self._store.upsert_model_settings_record(
                protocol=str(record["protocol"]),
                base_url=str(record["base_url"]),
                model=str(record["model"]),
                secret_payload_json=self._encode_credential_reference(reference),
                timeout_seconds=float(record["timeout_seconds"]),
                capability_verified_at=(
                    datetime.fromisoformat(str(record["capability_verified_at"]))
                    if record.get("capability_verified_at")
                    else None
                ),
                model_context_json=record.get("model_context_json"),
                reasoning_effort=record.get("reasoning_effort"),
            )
        except _CREDENTIAL_OPERATION_ERRORS:
            self._delete_protected_quietly(reference)
            raise StoredModelSettingsError(
                "stored model credentials are unavailable; retry migration or re-enter credentials"
            ) from None
        return reference, None

    def _promote_quarantined_secret(
        self,
        quarantine_payload_json: str,
        quarantine_reference: str,
    ) -> ModelSecrets:
        protected_payload = self._store.get_model_credential_quarantine(
            quarantine_reference
        )
        if protected_payload is None:
            raise StoredModelSettingsError(
                "stored model credentials are unavailable; retry migration or re-enter credentials"
            )
        unprotector = getattr(self._credential_store, "unprotect_quarantined", None)
        if not callable(unprotector):
            raise StoredModelSettingsError(
                "stored model credentials are unavailable; retry secure migration or re-enter credentials"
            )
        try:
            secret_payload = unprotector(protected_payload)
        except _CREDENTIAL_OPERATION_ERRORS:
            raise StoredModelSettingsError(
                "stored model credentials are unavailable; retry secure migration or re-enter credentials"
            ) from None
        if not isinstance(secret_payload, bytes):
            raise StoredModelSettingsError(
                "stored model credentials are invalid"
            )
        secrets = self._decode_secret_payload(secret_payload)
        try:
            reference = self._credential_store.put(secret_payload)
        except _CREDENTIAL_OPERATION_ERRORS:
            raise StoredModelSettingsError(
                "stored model credentials are unavailable; retry migration or re-enter credentials"
            ) from None
        try:
            self._store.replace_model_credential_quarantine(
                reference=quarantine_reference,
                expected_quarantine_payload_json=quarantine_payload_json,
                replacement_secret_payload_json=self._encode_credential_reference(
                    reference
                ),
            )
        except _CREDENTIAL_OPERATION_ERRORS:
            self._delete_protected_quietly(reference)
            raise StoredModelSettingsError(
                "stored model credentials are unavailable; retry migration or re-enter credentials"
            ) from None
        return secrets

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
        ):
            raise StoredModelSettingsError("stored model credentials are invalid") from None


def _encode_model_context(
    value: ModelContextV1 | dict[str, object] | None,
) -> str | None:
    if value is None:
        return None
    try:
        contract = value if isinstance(value, ModelContextV1) else ModelContextV1.model_validate(value)
        return json.dumps(
            contract.model_dump(mode="json", by_alias=False),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise StoredModelSettingsError("model context observation is invalid") from exc


def _decode_model_context(value: object) -> ModelContextV1 | None:
    if value is None:
        return None
    try:
        if not isinstance(value, str):
            raise TypeError("stored model context is not text")
        payload = json.loads(value)
        return ModelContextV1.model_validate(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        # A stale observation must never block ordinary model use.  It is
        # treated as unknown and will be replaced by the next capability test.
        return None


def _decode_reasoning_effort(value: object) -> ReasoningEffortV1 | None:
    if value is None:
        return None
    try:
        return validate_reasoning_effort(value)
    except ModelSettingsError as exc:
        raise StoredModelSettingsError("stored model reasoning effort is invalid") from exc


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


def validate_reasoning_effort(value: object) -> ReasoningEffortV1 | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in _REASONING_EFFORT_VALUES:
        raise ModelSettingsError(
            "reasoning_effort must be one of none, minimal, low, medium, high, "
            "xhigh, max, or ultra"
        )
    return value


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
