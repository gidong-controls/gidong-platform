"""NDJSON protocol shared by the Platform Host and the Agent sidecar.

This module has no MCP dependency.  It intentionally validates the small
supervision wire separately from the model/tool payloads so a malformed child
cannot be mistaken for an Agent result.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from controlstudio_contracts import AGENT_PROTOCOL_VERSION_V1, AgentManifestV1
from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_PROTOCOL_LINE_BYTES = 2_000_000


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
        "projectsha256",
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
