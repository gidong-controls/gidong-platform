from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

MNE_CAPABILITY_BUNDLE_ENV = "CONTROLSTUDIO_MNE_CAPABILITY_BUNDLE"
MNE_CAPABILITY_BUNDLE_SCHEMA = "controlstudio-mne-capability-bundle/v1"
MAX_MNE_CAPABILITY_BUNDLE_BYTES = 4 * 1024 * 1024
MAX_MNE_CAPABILITY_COMPONENT_BYTES = 2 * 1024 * 1024
MNE_CAPABILITY_AUTHORIZATION_SCHEMA = (
    "controlstudio-mne-capability-authorization/v1"
)
MNE_CAPABILITY_AUTHORIZATION_SCHEME = "CONTROLSTUDIO-HMAC-SHA256"
MNECapabilityStatus = Literal["missing", "valid", "expired", "runtime_mismatch"]


class MNECapabilityBundleError(ValueError):
    """The configured full-program replacement certificate is unavailable or invalid."""


@dataclass(frozen=True)
class MNECapabilityBundle:
    capability_receipt: dict[str, Any]
    runtime_identity: dict[str, Any]
    bundle_digest: str
    capability_receipt_digest: str
    runtime_identity_digest: str


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MNECapabilityBundleError(
                "the MNE capability bundle contains a duplicate JSON key"
            )
        result[key] = value
    return result


def _validate_json_value(value: object, *, depth: int = 0) -> None:
    if depth > 32:
        raise MNECapabilityBundleError("the MNE capability bundle is too deeply nested")
    if value is None or isinstance(value, bool | int):
        return
    if isinstance(value, float):
        if not (-1.0e308 < value < 1.0e308):
            raise MNECapabilityBundleError(
                "the MNE capability bundle contains a non-finite number"
            )
        return
    if isinstance(value, str):
        if len(value) > 1_048_576:
            raise MNECapabilityBundleError(
                "the MNE capability bundle contains an oversized string"
            )
        return
    if isinstance(value, list):
        if len(value) > 65_536:
            raise MNECapabilityBundleError(
                "the MNE capability bundle contains an oversized array"
            )
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 65_536 or any(
            not isinstance(key, str) or not key for key in value
        ):
            raise MNECapabilityBundleError(
                "the MNE capability bundle contains an invalid object"
            )
        for item in value.values():
            _validate_json_value(item, depth=depth + 1)
        return
    raise MNECapabilityBundleError(
        "the MNE capability bundle contains a non-JSON value"
    )


def load_mne_capability_bundle(
    path: str | os.PathLike[str] | None = None,
) -> MNECapabilityBundle:
    """Load a signed, developer-certified MNE capability without exposing it.

    The Host only performs bounded structural checks here. The MCP independently
    verifies the receipt HMAC, expiry, runtime identity, executable identity, and
    certified UI strategy immediately before it captures a production baseline.
    """

    configured = str(path or os.environ.get(MNE_CAPABILITY_BUNDLE_ENV, "")).strip()
    if not configured:
        raise MNECapabilityBundleError(
            f"{MNE_CAPABILITY_BUNDLE_ENV} is not configured"
        )
    bundle_path = Path(configured).expanduser().resolve()
    if not bundle_path.is_file():
        raise MNECapabilityBundleError(
            "the configured MNE capability bundle is not a regular file"
        )
    try:
        size = bundle_path.stat().st_size
    except OSError as exc:
        raise MNECapabilityBundleError(
            "the configured MNE capability bundle cannot be inspected"
        ) from exc
    if size <= 0 or size > MAX_MNE_CAPABILITY_BUNDLE_BYTES:
        raise MNECapabilityBundleError(
            "the configured MNE capability bundle has an invalid size"
        )
    try:
        raw = bundle_path.read_bytes()
    except OSError as exc:
        raise MNECapabilityBundleError(
            "the configured MNE capability bundle cannot be read"
        ) from exc
    if len(raw) != size:
        raise MNECapabilityBundleError(
            "the configured MNE capability bundle changed while it was read"
        )
    try:
        loaded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                MNECapabilityBundleError(
                    "the MNE capability bundle contains a non-finite number"
                )
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MNECapabilityBundleError(
            "the configured MNE capability bundle is not canonical UTF-8 JSON"
        ) from exc
    if not isinstance(loaded, dict) or set(loaded) != {
        "schema",
        "capabilityReceipt",
        "runtimeIdentity",
    }:
        raise MNECapabilityBundleError(
            "the configured MNE capability bundle has an unexpected shape"
        )
    if loaded.get("schema") != MNE_CAPABILITY_BUNDLE_SCHEMA:
        raise MNECapabilityBundleError(
            "the configured MNE capability bundle schema is unsupported"
        )
    receipt = loaded.get("capabilityReceipt")
    runtime = loaded.get("runtimeIdentity")
    if not isinstance(receipt, dict) or not isinstance(runtime, dict):
        raise MNECapabilityBundleError(
            "the MNE capability receipt and runtime identity must be objects"
        )
    _validate_json_value(loaded)
    receipt_bytes = _canonical_json_bytes(receipt)
    runtime_bytes = _canonical_json_bytes(runtime)
    if (
        len(receipt_bytes) > MAX_MNE_CAPABILITY_COMPONENT_BYTES
        or len(runtime_bytes) > MAX_MNE_CAPABILITY_COMPONENT_BYTES
    ):
        raise MNECapabilityBundleError(
            "the MNE capability receipt or runtime identity exceeds its bound"
        )
    unsigned_digest = str(receipt.get("unsigned_digest") or "").strip().casefold()
    if len(unsigned_digest) != 64 or any(
        character not in "0123456789abcdef" for character in unsigned_digest
    ):
        raise MNECapabilityBundleError(
            "the MNE capability receipt has no canonical unsigned digest"
        )
    return MNECapabilityBundle(
        capability_receipt=dict(receipt),
        runtime_identity=dict(runtime),
        bundle_digest=hashlib.sha256(_canonical_json_bytes(loaded)).hexdigest(),
        capability_receipt_digest=unsigned_digest,
        runtime_identity_digest=hashlib.sha256(runtime_bytes).hexdigest(),
    )


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise MNECapabilityBundleError("capability authorization timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MNECapabilityBundleError(
            "capability authorization timestamp is invalid"
        ) from exc
    if parsed.tzinfo is None:
        raise MNECapabilityBundleError(
            "capability authorization timestamp must include a timezone"
        )
    return parsed.astimezone(timezone.utc)


def inspect_mne_capability_bundle(
    *,
    authorization_key: bytes,
    path: str | os.PathLike[str] | None = None,
    now: datetime | None = None,
) -> MNECapabilityStatus:
    """Return a non-sensitive configured-bundle status.

    This is intentionally a pure file/HMAC check. Live XG5000 executable,
    selector, DPI, editor, and project identity are independently revalidated by
    MCP immediately before a replacement baseline is captured.
    """

    try:
        if not isinstance(authorization_key, bytes) or len(authorization_key) < 32:
            return "missing"
        bundle = load_mne_capability_bundle(path)
        receipt = bundle.capability_receipt
        if set(receipt) != {
            "schema",
            "receipt_id",
            "created_at",
            "enabled",
            "scratch",
            "runtime_identity",
            "strategy",
            "source_a",
            "source_b",
            "outcomes",
            "errors",
            "unsigned_digest",
            "authorization",
        }:
            return "missing"
        if receipt.get("enabled") is not True or receipt.get("errors") != []:
            return "missing"
        unsigned = {
            key: value
            for key, value in receipt.items()
            if key not in {"unsigned_digest", "authorization"}
        }
        unsigned_digest = str(receipt.get("unsigned_digest") or "").casefold()
        if (
            len(unsigned_digest) != 64
            or hashlib.sha256(_canonical_json_bytes(unsigned)).hexdigest()
            != unsigned_digest
        ):
            return "missing"
        envelope = receipt.get("authorization")
        if not isinstance(envelope, dict) or set(envelope) != {
            "scheme",
            "key_id",
            "signature",
            "signed_at",
            "expires_at",
        }:
            return "missing"
        if (
            envelope.get("scheme") != MNE_CAPABILITY_AUTHORIZATION_SCHEME
            or not isinstance(envelope.get("key_id"), str)
            or not envelope["key_id"].strip()
        ):
            return "missing"
        signed_at = _parse_utc(envelope.get("signed_at"))
        expires_at = _parse_utc(envelope.get("expires_at"))
        observed_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if signed_at > observed_now + timedelta(seconds=30):
            return "missing"
        if (
            expires_at <= signed_at
            or expires_at - signed_at > timedelta(days=30)
        ):
            return "missing"
        signing_payload = {
            "schema": MNE_CAPABILITY_AUTHORIZATION_SCHEMA,
            "unsigned_digest": unsigned_digest,
            "scheme": envelope["scheme"],
            "key_id": envelope["key_id"],
            "signed_at": envelope["signed_at"],
            "expires_at": envelope["expires_at"],
        }
        expected_signature = hmac.new(
            authorization_key,
            _canonical_json_bytes(signing_payload),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(
            str(envelope.get("signature") or "").casefold(),
            expected_signature,
        ):
            return "missing"
        if observed_now >= expires_at:
            return "expired"
        if _canonical_json_bytes(receipt.get("runtime_identity")) != (
            _canonical_json_bytes(bundle.runtime_identity)
        ):
            return "runtime_mismatch"
        return "valid"
    except (OSError, TypeError, ValueError):
        return "missing"


__all__ = [
    "MNE_CAPABILITY_BUNDLE_ENV",
    "MNECapabilityStatus",
    "MNECapabilityBundle",
    "MNECapabilityBundleError",
    "inspect_mne_capability_bundle",
    "load_mne_capability_bundle",
]
