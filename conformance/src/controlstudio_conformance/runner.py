"""Black-box checks for Agent Protocol v1 and MCP Product Contract v1."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any

from typing_extensions import Self


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


@dataclass
class Stdio:
    command: list[str]
    process: subprocess.Popen[str] | None = None

    def __enter__(self) -> Self:
        self.process = subprocess.Popen(self.command, text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return self

    def request(self, value: dict[str, Any]) -> dict[str, Any]:
        assert self.process and self.process.stdin and self.process.stdout
        self.process.stdin.write(json.dumps(value, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        return self.read()

    def read(self) -> dict[str, Any]:
        assert self.process and self.process.stdout
        line = self.process.stdout.readline()
        if not line:
            raise AssertionError("component closed stdout before a protocol response")
        return json.loads(line)

    def __exit__(self, *_: object) -> None:
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
            self.process.communicate(timeout=5)


def check_agent(command: list[str]) -> None:
    def envelope(message_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"protocol_version": "1.0", "type": message_type, "payload": dict(payload or {})}

    def assert_envelope(value: dict[str, Any], expected: str) -> dict[str, Any]:
        assert set(value) == {"protocol_version", "type", "payload"}
        assert value["protocol_version"] == "1.0" and value["type"] == expected
        assert isinstance(value["payload"], dict)
        return value["payload"]

    with Stdio(command) as sidecar:
        initialize = assert_envelope(sidecar.request(envelope("initialize")), "initialized")
        manifest = initialize["manifest"]
        assert manifest["protocol_version"] == "1.0" and manifest["agent_id"]
        health = assert_envelope(sidecar.request(envelope("health")), "health.result")
        assert health["status"] == "ready"
        if manifest["agent_id"] != "controlstudio-public-mock-agent":
            synced = assert_envelope(sidecar.request(envelope("knowledge.sync", {
                "documents": [{
                    "reference": "knowledge://conformance/product-agent",
                    "content": "ControlStudio packaged Agent conformance knowledge record.",
                }],
            })), "knowledge.synced")
            assert synced["count"] == 1
            search = assert_envelope(sidecar.request(envelope("knowledge.search", {
                "query": "packaged Agent conformance", "limit": 1,
            })), "knowledge.search.result")
            assert search["results"] and search["results"][0]["knowledge_ref"] == "knowledge://conformance/product-agent"
            assert search["untrusted_reference_data"] is True
            assert_envelope(sidecar.request(envelope("shutdown")), "shutdown.complete")
            return
        capability = assert_envelope(sidecar.request(envelope("capabilities.test", {"request_id": "cap-1", "model": {}})), "capabilities.result")
        assert capability["request_id"] == "cap-1" and capability["result"]["success"] is True
        started = sidecar.request(envelope("run.start", {
            "run_id": "run-1", "conversation_id": "conversation-1", "prompt": "inspect",
            "history": [], "model": {}, "tools": [{"protocol_version": "1.0", "tool_name": "workspace.inspect", "parameters_json_schema": {"type": "object"}}],
        }))
        assert_envelope(started, "run.started")
        request = assert_envelope(sidecar.read(), "tool.request")
        required = {"protocol_version", "request_id", "conversation_id", "run_id", "tool_name", "tool_call_id", "arguments"}
        assert required <= request.keys() and request["protocol_version"] == "1.0"
        assert "target" not in json.dumps(request).lower() and "approval" not in json.dumps(request).lower()
        final = assert_envelope(sidecar.request(envelope("tool.result", {
            "request_id": request["request_id"], "status": "completed", "result": {"summary": "ok"},
        })), "run.final")
        output = final["output"]
        assert final["run_id"] == "run-1" and {"content", "model", "input_tokens", "output_tokens", "workflow"} <= output.keys()
        cancel_started = sidecar.request(envelope("run.start", {"run_id": "run-2", "conversation_id": "conversation-1", "prompt": "cancel", "history": [], "model": {}, "tools": []}))
        assert_envelope(cancel_started, "run.started")
        assert_envelope(sidecar.read(), "tool.request")
        accepted = assert_envelope(sidecar.request(envelope("run.cancel", {"run_id": "run-2"})), "run.cancel.accepted")
        assert accepted["run_id"] == "run-2"
        assert_envelope(sidecar.read(), "run.cancelled")
        assert_envelope(sidecar.request(envelope("shutdown")), "shutdown.complete")


def check_mcp(command: list[str]) -> None:
    with Stdio(command) as server:
        initialized = server.request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}})
        assert initialized["result"]["serverInfo"]["name"]
        response = server.request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "controlstudio.contract_manifest", "arguments": {}}})
        manifest_response = response["result"]["structuredContent"]
        manifest = manifest_response.get("data") if manifest_response.get("status") == "ok" else manifest_response
        assert isinstance(manifest, dict)
        digest_input = {
            key: value for key, value in manifest.items()
            if key not in {"manifest_digest", "schema_version"}
        }
        assert manifest["contract_version"] == "1.0" and manifest["manifest_digest"] == canonical_digest(digest_input)
        listed = server.request({"jsonrpc": "2.0", "id": 22, "method": "tools/list", "params": {}})
        schemas = {
            tool["name"]: {
                "inputSchema": tool["inputSchema"],
                "outputSchema": tool.get("outputSchema"),
            }
            for tool in listed["result"]["tools"]
        }
        assert set(schemas) == {tool["tool_name"] for tool in manifest["tools"]}
        for tool in manifest["tools"]:
            assert re.fullmatch(r"[a-f0-9]{64}", tool["schema_digest"])
            assert tool["schema_digest"] == canonical_digest(schemas[tool["tool_name"]])
        if "project.apply_mock" in schemas:
            assert {"controlstudio.contract_manifest", "workspace.inspect", "project.apply_mock"} <= set(schemas)
            mutation_contract = next(tool for tool in manifest["tools"] if tool["tool_name"] == "project.apply_mock")
            assert mutation_contract["approval_required"] is True and mutation_contract["target_binding_required"] is True
            mutation = server.request({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "project.apply_mock", "arguments": {"change": "no-op"}}})
            assert mutation["result"]["isError"] is True
            assert mutation["result"]["structuredContent"]["failure_kind"] == "approval_required"
            return
        assert manifest["product"] == "ls-xg5000"
        assert {
            "controlstudio.contract_manifest", "xg5000.instances", "xg5000.attach",
            "xg5000.session_status", "project.list_programs", "program.read_ld", "program.logic_graph",
        } <= set(schemas)
        instances = server.request({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "xg5000.instances", "arguments": {}}})
        result = instances["result"]
        assert result.get("isError") is not True
        structured = result["structuredContent"]
        assert structured.get("status") == "ok" and isinstance(structured.get("data", {}).get("instances"), list)


def parse_command(value: str) -> list[str]:
    """Accept JSON argv when transport preserves quotes, or a simple command."""
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
            return parsed
    except json.JSONDecodeError:
        pass
    parsed = shlex.split(value, posix=False)
    if not parsed:
        raise ValueError("command must not be empty")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-command", required=True, help="JSON argv array or simple command string")
    parser.add_argument("--mcp-command", required=True, help="JSON argv array or simple command string")
    args = parser.parse_args()
    agent_command, mcp_command = parse_command(args.agent_command), parse_command(args.mcp_command)
    check_agent(agent_command)
    check_mcp(mcp_command)
    print(json.dumps({"passed": True, "agent_protocol": "1.0", "mcp_product_contract": "1.0"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
