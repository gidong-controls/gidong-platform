"""NDJSON protocol shared by the Platform Host and the Agent sidecar.

This module has no MCP dependency.  It intentionally validates the small
supervision wire separately from the model/tool payloads so a malformed child
cannot be mistaken for an Agent result.
"""

from __future__ import annotations

import json
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
        "authorization_secret",
        "approval_secret",
        "approval_token",
        "session_id",
        "pid",
        "hwnd",
        "project_path",
        "project_hash",
        "engineering_process_id",
        "engineering_window_handle",
        "controller_endpoint",
    }
)

_REDACTED_AGENT_RESULT_FIELDS = _FORBIDDEN_AGENT_PAYLOAD_FIELDS | frozenset(
    {
        # Controller observation is Host-owned too.  The Agent receives the
        # observation payload, never the connected endpoint selected by the UI.
        "endpoint",
        "host",
        "port",
        "timeout_seconds",
    }
)


def encode_message(message_type: str, payload: Mapping[str, Any] | None = None) -> bytes:
    """Encode exactly one protocol frame, suitable for a binary stdin/stdout pipe."""

    message = AgentProtocolMessage(
        protocol_version=AGENT_PROTOCOL_VERSION_V1,
        type=message_type,
        payload=dict(payload or {}),
    )
    encoded = json.dumps(
        message.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_PROTOCOL_LINE_BYTES:
        raise AgentProtocolError("Agent protocol message exceeds the allowed size")
    return encoded + b"\n"


def decode_message(line: bytes) -> AgentProtocolMessage:
    """Decode and version-check one sidecar stdout or Host stdin frame."""

    if not line or len(line) > MAX_PROTOCOL_LINE_BYTES:
        raise AgentProtocolError("Agent protocol line is missing or too large")
    try:
        raw = json.loads(line.decode("utf-8"))
        message = AgentProtocolMessage.model_validate(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
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
                if isinstance(key, str) and key.casefold() in _FORBIDDEN_AGENT_PAYLOAD_FIELDS:
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
            or key.casefold() not in _REDACTED_AGENT_RESULT_FIELDS
        }
    if isinstance(value, list):
        return [scrub_agent_tool_result(item) for item in value]
    if isinstance(value, tuple):
        return [scrub_agent_tool_result(item) for item in value]
    return value
