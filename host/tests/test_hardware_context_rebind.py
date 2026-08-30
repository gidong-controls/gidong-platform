import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from controlstudio_contracts import ExternalToolRequestV1

from controlstudio_host.agent_client import project_historical_evidence
from controlstudio_host.app import _journaled_hardware_context_evidence_for_mission
from controlstudio_host.execution_broker import (
    MCPExecutionBroker,
    _canonical_digest,
)
from controlstudio_host.mcp_client import MCPGateway, MCPToolError, MCPToolRejectedError

_PROJECT_HASH = "a" * 64


def _raw_hardware_context(
    *,
    marker: str = "same",
    cpu_status: str = "partial",
) -> dict[str, Any]:
    blockers = [] if cpu_status == "observed" else ["cpu_model_unobserved"]
    unsigned = {
        "schema": "xg5000.project.hardware-context/1.0",
        "project_binding": {"project_hash": _PROJECT_HASH, "marker": marker},
        "cpu": {"status": cpu_status},
        "timer_areas": [],
        "io_modules": [],
        "io_mapping": {},
        "external_wiring_observed": False,
        "blockers": blockers,
        "offline_candidate_ready": not blockers,
    }
    return {**unsigned, "context_digest": _canonical_digest(unsigned)}


class _Gateway:
    def __init__(self, *, rebound: dict[str, Any], first_error: str) -> None:
        self._identity = {
            "session_id": "session-1",
            "pid": 123,
            "hwnd": 456,
            "project_path": r"C:\\work\\target.xgwx",
            "project_hash": _PROJECT_HASH,
        }
        self.rebound = rebound
        self.first_error = first_error
        self.calls: list[str] = []
        self.call_arguments: list[dict[str, Any]] = []
        self.analysis_calls = 0

    @property
    def agent_session_identity_snapshot(self) -> dict[str, Any]:
        return dict(self._identity)

    async def execute_agent_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append(name)
        self.call_arguments.append(dict(arguments))
        if name == "program.change_target_analysis":
            self.analysis_calls += 1
            if self.analysis_calls == 1:
                raise MCPToolRejectedError(self.first_error)
            return {"analysis": "retried-once"}
        if name == "project.hardware_context":
            return self.rebound
        raise AssertionError(name)


def _request(context_digest: str) -> ExternalToolRequestV1:
    return ExternalToolRequestV1(
        request_id="hardware-rebind-request",
        conversation_id="hardware-rebind-conversation",
        run_id="hardware-rebind-run",
        tool_name="analyze_change_target",
        arguments={"hardware_context_digest": context_digest},
    )


async def _sink(_event_type: str, _payload: dict[str, Any]) -> None:
    return None


class HardwareContextRebindTests(unittest.IsolatedAsyncioTestCase):
    def _broker(self, gateway: _Gateway, retained: dict[str, Any]) -> MCPExecutionBroker:
        broker = MCPExecutionBroker(cast(MCPGateway, gateway))
        retained_contract = broker._hardware_context_evidence_contract(retained)
        broker.bind_retained_hardware_context_evidence(
            conversation_id="hardware-rebind-conversation",
            run_id="hardware-rebind-run",
            evidence=[
                {
                    "toolName": "project.hardware_context",
                    "receipt": retained_contract,
                }
            ],
        )
        return broker

    async def test_exact_cache_miss_rebinds_read_only_context_and_retries_once(self) -> None:
        retained = _raw_hardware_context()
        gateway = _Gateway(
            rebound=retained,
            first_error=(
                "program.change_target_analysis was rejected by MCP: "
                '{"schema_version":"1.0","status":"error","error":'
                '{"type":"SessionError","message":"project.hardware_context must be called before binding its digest"}}'
            ),
        )
        broker = self._broker(gateway, retained)

        with patch.object(MCPExecutionBroker, "_evidence_contract", return_value=None):
            result = await broker.execute_external_tool_request(
                _request(retained["context_digest"]),
                sink=_sink,
            )

        self.assertEqual(result, {"analysis": "retried-once"})
        self.assertEqual(
            gateway.calls,
            [
                "program.change_target_analysis",
                "project.hardware_context",
                "program.change_target_analysis",
            ],
        )

    async def test_verified_reopen_rebinds_new_session_digest_before_retry(self) -> None:
        retained = _raw_hardware_context(marker="old-session")
        rebound = _raw_hardware_context(marker="new-session")
        self.assertNotEqual(retained["context_digest"], rebound["context_digest"])
        gateway = _Gateway(
            rebound=rebound,
            first_error=(
                "program.change_target_analysis was rejected by MCP: "
                '{"schema_version":"1.0","status":"error","error":'
                '{"type":"SessionError","message":"project.hardware_context must be called before binding its digest"}}'
            ),
        )
        broker = self._broker(gateway, retained)

        with patch.object(MCPExecutionBroker, "_evidence_contract", return_value=None):
            result = await broker.execute_external_tool_request(
                _request(retained["context_digest"]),
                sink=_sink,
            )

        self.assertEqual(result, {"analysis": "retried-once"})
        self.assertEqual(
            gateway.call_arguments[-1]["hardware_context_digest"],
            rebound["context_digest"],
        )

    async def test_real_tool_error_json_shape_rebinds_and_retries_once(self) -> None:
        retained = _raw_hardware_context()
        gateway = _Gateway(
            rebound=retained,
            first_error=(
                "program.change_target_analysis was rejected by MCP: "
                '{"schema_version":"1.0","status":"error","error":'
                '{"type":"SessionError","message":"project.hardware_context must be called before binding its digest"}}'
            ),
        )
        broker = self._broker(gateway, retained)

        with patch.object(MCPExecutionBroker, "_evidence_contract", return_value=None):
            result = await broker.execute_external_tool_request(
                _request(retained["context_digest"]),
                sink=_sink,
            )

        self.assertEqual(result, {"analysis": "retried-once"})
        self.assertEqual(gateway.analysis_calls, 2)
        self.assertEqual(
            gateway.calls,
            [
                "program.change_target_analysis",
                "project.hardware_context",
                "program.change_target_analysis",
            ],
        )

    async def test_typed_tool_error_payload_requires_the_exact_contract(self) -> None:
        exact = {
            "schema_version": "1.0",
            "status": "error",
            "error": {
                "type": "SessionError",
                "message": "project.hardware_context must be called before binding its digest",
            },
        }
        self.assertTrue(
            MCPExecutionBroker._is_hardware_context_binding_precondition(
                MCPToolRejectedError("opaque", mcp_error_payload=exact)
            )
        )
        self.assertFalse(
            MCPExecutionBroker._is_hardware_context_binding_precondition(
                MCPToolRejectedError(
                    "opaque",
                    mcp_error_payload={**exact, "extra": True},
                )
            )
        )

    async def test_other_mcp_errors_do_not_trigger_a_rebind(self) -> None:
        retained = _raw_hardware_context()
        gateway = _Gateway(
            rebound=retained,
            first_error="program.change_target_analysis was rejected by MCP: unrelated",
        )
        broker = self._broker(gateway, retained)

        with self.assertRaises(MCPToolRejectedError):
            await broker.execute_external_tool_request(
                _request(retained["context_digest"]),
                sink=_sink,
            )
        self.assertEqual(gateway.calls, ["program.change_target_analysis"])

    async def test_context_digest_mismatch_blocks_without_analyze_retry(self) -> None:
        retained = _raw_hardware_context()
        gateway = _Gateway(
            rebound=_raw_hardware_context(
                marker="new-session",
                cpu_status="observed",
            ),
            first_error=(
                "program.change_target_analysis was rejected by MCP: "
                '{"schema_version":"1.0","status":"error","error":'
                '{"type":"SessionError","message":"project.hardware_context must be called before binding its digest"}}'
            ),
        )
        broker = self._broker(gateway, retained)

        with self.assertRaisesRegex(MCPToolError, "differs from retained"):
            await broker.execute_external_tool_request(
                _request(retained["context_digest"]),
                sink=_sink,
            )
        self.assertEqual(
            gateway.calls,
            ["program.change_target_analysis", "project.hardware_context"],
        )

    async def test_restart_rebind_uses_journaled_not_model_projected_hardware_receipt(
        self,
    ) -> None:
        retained = _raw_hardware_context()
        gateway = _Gateway(rebound=retained, first_error="unused")
        broker = MCPExecutionBroker(cast(MCPGateway, gateway))
        contract = broker._hardware_context_evidence_contract(retained)
        journaled = {
            "evidenceId": "hardware-evidence-id",
            "toolName": "project.hardware_context",
            "receipt": contract,
        }

        # The Agent-safe historical projection deliberately withholds Host-only
        # projectHash, so it must never feed the session-rebind boundary.
        projected = project_historical_evidence(journaled)
        projected_receipt = projected["receipt"]
        self.assertNotIn("projectHash", projected_receipt)

        class _Store:
            def resolve_conversation_evidence(self, **kwargs: Any) -> dict[str, Any]:
                self.kwargs = kwargs
                return {"hardware-evidence-id": SimpleNamespace(payload=journaled)}

        store = _Store()
        mission = {
            "steps": [
                {"evidenceRefs": ["hardware-evidence-id"]},
            ]
        }
        exact = _journaled_hardware_context_evidence_for_mission(
            cast(Any, store),
            conversation_id="hardware-rebind-conversation",
            mission=mission,
        )
        self.assertEqual(exact, [journaled])
        self.assertEqual(store.kwargs["evidence_ids"], {"hardware-evidence-id"})
        broker.bind_retained_hardware_context_evidence(
            conversation_id="hardware-rebind-conversation",
            run_id="hardware-rebind-run",
            evidence=exact,
        )


if __name__ == "__main__":
    unittest.main()
