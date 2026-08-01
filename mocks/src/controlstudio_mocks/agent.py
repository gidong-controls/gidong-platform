"""Deterministic NDJSON implementation of the public Agent Protocol v1."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

from .common import json_line

PROTOCOL_VERSION = "1.0"
MANIFEST = {
    "protocol_version": PROTOCOL_VERSION,
    "agent_id": "controlstudio-public-mock-agent",
    "capabilities": [
        "health",
        "run.start",
        "run.cancel",
        "external-tool-request",
        "capability-test",
        "knowledge-sync",
        "shutdown",
    ],
}


def frame(message_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"protocol_version": PROTOCOL_VERSION, "type": message_type, "payload": dict(payload or {})}


def validate_frame(value: object) -> tuple[str, dict[str, Any]]:
    """Apply the Host envelope's version and extra-forbid semantics."""
    if not isinstance(value, dict) or set(value) != {"protocol_version", "type", "payload"}:
        raise ValueError("Agent Protocol envelope must contain only protocol_version, type, payload")
    if value["protocol_version"] != PROTOCOL_VERSION:
        raise ValueError("unsupported Agent Protocol version")
    if not isinstance(value["type"], str) or not value["type"] or not isinstance(value["payload"], dict):
        raise ValueError("invalid Agent Protocol type or payload")
    return value["type"], value["payload"]


class MockAgent:
    def __init__(self) -> None:
        self.run_id: str | None = None
        self.conversation_id: str | None = None
        self.tool_request_id: str | None = None
        self.tool_name: str | None = None

    def handle(self, value: object) -> list[dict[str, Any]]:
        try:
            message_type, payload = validate_frame(value)
            return self._handle(message_type, payload)
        except ValueError:
            return [frame("protocol.error", {"code": "invalid_message"})]

    def _handle(self, message_type: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if message_type == "initialize":
            return [frame("initialized", {"manifest": MANIFEST})]
        if message_type == "health":
            return [frame("health.result", {"status": "ready", "agent_id": MANIFEST["agent_id"]})]
        if message_type == "knowledge.sync":
            documents = payload.get("documents")
            if not isinstance(documents, list):
                raise ValueError("knowledge documents must be a list")
            return [frame("knowledge.synced", {"count": len(documents)})]
        if message_type == "capabilities.test":
            request_id = _text(payload, "request_id")
            return [frame("capabilities.result", {"request_id": request_id, "result": {
                "success": True,
                "tested_at": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
                "completion": True,
                "streaming": True,
                "tool_calling": True,
                "tool_result_continuation": True,
                "structured_output": True,
                "message": "Public mock capabilities are deterministic.",
            }})]
        if message_type == "run.start":
            if self.run_id is not None:
                return [frame("run.error", {"run_id": str(payload.get("run_id") or ""), "code": "agent_busy"})]
            self.run_id = _text(payload, "run_id")
            self.conversation_id = _text(payload, "conversation_id")
            tools = payload.get("tools")
            self.tool_name = _first_tool_name(tools) or "workspace.inspect"
            self.tool_request_id = f"mock-tool-{self.run_id}"
            return [
                frame("run.started", {"run_id": self.run_id}),
                frame("tool.request", {
                    "protocol_version": PROTOCOL_VERSION,
                    "request_id": self.tool_request_id,
                    "conversation_id": self.conversation_id,
                    "run_id": self.run_id,
                    "tool_name": self.tool_name,
                    "tool_call_id": "mock-call-1",
                    "arguments": {},
                }),
            ]
        if message_type == "tool.result":
            if self.run_id is None or payload.get("request_id") != self.tool_request_id:
                raise ValueError("tool result is not bound to the active mock request")
            status = payload.get("status")
            if status not in {"completed", "failed"}:
                raise ValueError("tool result status is invalid")
            run_id = self.run_id
            self._clear_run()
            return [frame("run.final", {"run_id": run_id, "output": {
                "content": "Deterministic mock tool result accepted." if status == "completed" else "Deterministic mock tool failure handled.",
                "model": "controlstudio-public-mock-agent",
                "input_tokens": 0,
                "output_tokens": 0,
                "workflow": "engineering",
            }})]
        if message_type == "run.cancel":
            requested = _text(payload, "run_id")
            replies = [frame("run.cancel.accepted", {"run_id": requested})]
            if self.run_id == requested:
                self._clear_run()
                replies.append(frame("run.cancelled", {"run_id": requested}))
            return replies
        if message_type == "shutdown":
            self._clear_run()
            return [frame("shutdown.complete", {"status": "stopped"})]
        raise ValueError("unsupported Agent Protocol command")

    def _clear_run(self) -> None:
        self.run_id = self.conversation_id = self.tool_request_id = self.tool_name = None


def _text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} is required")
    return value


def _first_tool_name(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    for tool in value:
        if isinstance(tool, dict) and isinstance(tool.get("tool_name"), str) and tool["tool_name"]:
            return tool["tool_name"]
    return None


def main() -> int:
    mock = MockAgent()
    for raw in sys.stdin:
        try:
            incoming: object = json.loads(raw)
        except json.JSONDecodeError:
            incoming = None
        replies = mock.handle(incoming)
        for reply in replies:
            sys.stdout.write(json_line(reply) + "\n")
            sys.stdout.flush()
        if replies and replies[-1]["type"] == "shutdown.complete":
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
