"""NDJSON protocol shared by the Platform Host and the Agent sidecar.

This module has no MCP dependency.  It intentionally validates the small
supervision wire separately from the model/tool payloads so a malformed child
cannot be mistaken for an Agent result.
"""

from __future__ import annotations

import json
import hashlib
import math
from collections.abc import Mapping
from typing import Any

from controlstudio_contracts import AGENT_PROTOCOL_VERSION_V1, AgentManifestV1
from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_PROTOCOL_LINE_BYTES = 2_000_000


def validated_transcript_checkpoint(
    checkpoint: Mapping[str, Any], digest: str | None = None,
) -> tuple[dict[str, Any], str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate historical, fully settled messages; this grants no authority.

    SDK request envelopes may merge on continuation. Monotonic persistence
    therefore compares exact tool parts and user prompts, not message counts.
    """
    if set(checkpoint) != {"schemaVersion", "messages"} or checkpoint.get("schemaVersion") != "1":
        raise AgentProtocolError("Agent transcript checkpoint shape is invalid")
    _assert_finite_protocol_numbers(checkpoint)
    try:
        encoded = json.dumps(checkpoint, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        canonical = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise AgentProtocolError("Agent transcript checkpoint is not strict JSON") from exc
    actual_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    if digest is not None and digest != actual_digest:
        raise AgentProtocolError("Agent transcript checkpoint digest mismatched")
    messages = canonical["messages"]
    if not isinstance(messages, list) or not messages:
        raise AgentProtocolError("Agent transcript messages are invalid")
    pending: dict[str, str] = {}
    tool_parts: list[dict[str, Any]] = []
    user_parts: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("kind") not in {"request", "response"}:
            raise AgentProtocolError("Agent transcript message is invalid")
        parts = message.get("parts")
        if not isinstance(parts, list):
            raise AgentProtocolError("Agent transcript message parts are invalid")
        if pending and message["kind"] != "request":
            raise AgentProtocolError("Agent transcript has an unsettled tool exchange")
        for part in parts:
            if not isinstance(part, dict) or not isinstance(part.get("part_kind"), str):
                raise AgentProtocolError("Agent transcript part is invalid")
            kind = part["part_kind"]
            if kind == "thinking":
                raise AgentProtocolError("Agent transcript contains private thinking")
            if kind == "user-prompt":
                user_parts.append(part)
            if kind == "tool-call":
                call_id, name = part.get("tool_call_id"), part.get("tool_name")
                if message["kind"] != "response" or not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name or call_id in pending:
                    raise AgentProtocolError("Agent transcript tool call is invalid")
                pending[call_id] = name
                tool_parts.append(part)
            elif kind == "tool-return" or (kind == "retry-prompt" and part.get("tool_call_id")):
                call_id = part.get("tool_call_id")
                if message["kind"] != "request" or call_id not in pending or (
                    part.get("tool_name") != pending[call_id]
                    and not (kind == "retry-prompt" and part.get("tool_name") is None)
                ):
                    raise AgentProtocolError("Agent transcript tool result is unpaired")
                del pending[call_id]
                tool_parts.append(part)
            elif kind == "retry-prompt" and part.get("tool_name") is not None:
                raise AgentProtocolError("Agent transcript retry is unpaired")
    if pending:
        raise AgentProtocolError("Agent transcript has unfinished tool calls")
    return canonical, actual_digest, tool_parts, user_parts


class AgentProtocolError(RuntimeError):
    """The sidecar sent an invalid, unsafe, or incompatible protocol frame."""


class AgentProtocolMessage(BaseModel):
    """Strict outer envelope for a single newline-delimited JSON frame."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: str = Field(min_length=1, max_length=32)
    type: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)


_FORBIDDEN_AGENT_PAYLOAD_FIELDS = frozenset(
    {
        "authorizationsecret",
        "approvalsecret",
        "approvaltoken",
        "sessionid",
        "pid",
        "hwnd",
        "projectpath",
        "projecthash",
        "engineeringprocessid",
        "engineeringwindowhandle",
        "controllerendpoint",
    }
)

_REDACTED_AGENT_RESULT_FIELDS = _FORBIDDEN_AGENT_PAYLOAD_FIELDS | frozenset(
    {
        # Tool results never carry model or service credentials.  Normalize
        # spelling first so snake/camel/kebab variants share one policy.
        "apikey",
        "accesstoken",
        "authtoken",
        "authorization",
        "authorizationheader",
        "authorizationtoken",
        "cookie",
        "csrftoken",
        "credential",
        "credentials",
        "customheaders",
        "password",
        "passwd",
        "privatekey",
        "proxyauthorization",
        "refreshtoken",
        "secret",
        "secretkey",
        "setcookie",
        "signingkey",
        "sessiontoken",
        "token",
        # Controller observation is Host-owned too.  The Agent receives the
        # observation payload, never the connected endpoint selected by the UI.
        "endpoint",
        "host",
        "port",
        "timeoutseconds",
        # MCP analysis artifacts stay Host-owned. The model receives their
        # structured facts and evidence receipt, never local artifact paths.
        "payloadpath",
        "markdownpath",
        "receiptpath",
    }
)


def _normalized_agent_field_name(value: str) -> str:
    """Collapse snake/camel/kebab spelling before applying boundary policy."""

    return "".join(character for character in value.casefold() if character.isalnum())


def _is_redacted_agent_result_field(value: str) -> bool:
    normalized = _normalized_agent_field_name(value)
    return (
        normalized in _REDACTED_AGENT_RESULT_FIELDS
        or normalized.endswith(
            (
                "apikey",
                "accesstoken",
                "authtoken",
                "authorizationtoken",
                "bearertoken",
                "credential",
                "credentials",
                "csrftoken",
                "idtoken",
                "password",
                "passwd",
                "refreshtoken",
                "secret",
                "sessiontoken",
            )
        )
        or normalized == "path"
        or normalized.endswith("path")
        or normalized.endswith("directory")
    )


def _assert_finite_protocol_numbers(value: Any) -> None:
    """Reject JSON extensions that would otherwise be rewritten as null."""

    if isinstance(value, float):
        if not math.isfinite(value):
            raise AgentProtocolError("Agent protocol payload is not strict JSON")
        return
    if isinstance(value, Mapping):
        for nested in value.values():
            _assert_finite_protocol_numbers(nested)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for nested in value:
            _assert_finite_protocol_numbers(nested)


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def encode_message(message_type: str, payload: Mapping[str, Any] | None = None) -> bytes:
    """Encode exactly one protocol frame, suitable for a binary stdin/stdout pipe."""

    raw_payload = dict(payload or {})
    _assert_finite_protocol_numbers(raw_payload)
    message = AgentProtocolMessage(
        protocol_version=AGENT_PROTOCOL_VERSION_V1,
        type=message_type,
        payload=raw_payload,
    )
    encoded = json.dumps(
        message.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    framed = encoded + b"\n"
    if len(framed) > MAX_PROTOCOL_LINE_BYTES:
        raise AgentProtocolError("Agent protocol message exceeds the allowed size")
    return framed


def decode_message(line: bytes) -> AgentProtocolMessage:
    """Decode and version-check one sidecar stdout or Host stdin frame."""

    if not line or len(line) > MAX_PROTOCOL_LINE_BYTES:
        raise AgentProtocolError("Agent protocol line is missing or too large")
    try:
        raw = json.loads(
            line.decode("utf-8"),
            parse_constant=_reject_non_finite_json_constant,
        )
        message = AgentProtocolMessage.model_validate(raw)
    except (UnicodeDecodeError, ValueError, ValidationError) as exc:
        raise AgentProtocolError("Agent protocol frame is invalid") from exc
    if message.protocol_version != AGENT_PROTOCOL_VERSION_V1:
        raise AgentProtocolError("Agent protocol version is not supported")
    return message


def parse_manifest(payload: Mapping[str, Any]) -> AgentManifestV1:
    try:
        manifest = AgentManifestV1.model_validate(payload)
    except ValidationError as exc:
        raise AgentProtocolError("Agent manifest is invalid") from exc
    if manifest.protocol_version != AGENT_PROTOCOL_VERSION_V1:
        raise AgentProtocolError("Agent manifest version is not supported")
    return manifest


def assert_agent_safe_payload(value: Any) -> None:
    """Reject target identity and approval material before it reaches the child.

    This guards the explicit protocol boundary; it is not a replacement for
    the Host's MCP-side injection and stale-value checks.
    """

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if (
                    isinstance(key, str)
                    and _normalized_agent_field_name(key)
                    in _FORBIDDEN_AGENT_PAYLOAD_FIELDS
                ):
                    raise AgentProtocolError(
                        "Host attempted to send protected target or approval data to Agent"
                    )
                visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)

    visit(value)


def scrub_agent_tool_result(value: Any) -> Any:
    """Remove Host-owned target and endpoint fields from a tool response.

    This is applied immediately before Host sends an external-tool result to
    the sidecar.  It complements (rather than replaces) schema removal on the
    request path, since a vendor read result may echo injected arguments.
    """

    if isinstance(value, Mapping):
        return {
            key: scrub_agent_tool_result(item)
            for key, item in value.items()
            if not isinstance(key, str)
            or not _is_redacted_agent_result_field(key)
        }
    if isinstance(value, list):
        return [scrub_agent_tool_result(item) for item in value]
    if isinstance(value, tuple):
        return [scrub_agent_tool_result(item) for item in value]
    return value
