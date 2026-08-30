from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from controlstudio_contracts import ExternalToolRequestV1

from controlstudio_host.agent_client import (
    AgentProtocolError,
    AgentSupervisor,
    SubprocessAgentClient,
    project_historical_evidence,
)
from controlstudio_host.app import _agent_tool_evidence_ref
from controlstudio_host.execution_broker import (
    MCPExecutionBroker,
    _canonical_digest,
    _validate_workspace_status_lineage,
)
from controlstudio_host.mcp_client import (
    MCPGateway,
    MCPStatus,
    MCPToolError,
    MCPToolRejectedError,
    MCPTransportError,
)
from controlstudio_host.model_connection import (
    MODEL_PROTOCOL,
    ModelSecrets,
    StoredModelConnection,
)
from controlstudio_host.structural_evidence import (
    STRUCTURAL_ANALYSIS_EVIDENCE_SCHEMA,
    validate_project_search_context,
)

_ACCEPTANCE_ENV = "CONTROLSTUDIO_RUN_XGWX_ACCEPTANCE"
_XGWX_ENV = "CONTROLSTUDIO_ACCEPTANCE_XGWX"
_PRESERVED_XGWX_SHA256 = "bee3b1a6e69469c0e1384cb8d9d758fef2f95a92f0cc7ac6486deea941a9a68f"
_STALE_REVISION_ID = "00000000-0000-4000-8000-000000000000"
_PLATFORM_ROOT = Path(__file__).resolve().parents[2]
_CHECKOUT_ROOT = _PLATFORM_ROOT.parent


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _workspace_static_binding(workspace: Mapping[str, Any]) -> dict[str, Any]:
    source = workspace["source"]
    working = workspace["working_copy"]
    provenance = workspace.get("provenance")
    return {
        "workspace_id": workspace["workspace_id"],
        "revision_id": workspace["revision_id"],
        "source_kind": workspace["source_kind"],
        "provenance_receipt_digest": (
            provenance.get("receipt_digest") if isinstance(provenance, Mapping) else None
        ),
        "source_sha256": source["sha256"],
        "working_path": working["path"],
        "working_sha256": working["current_sha256"],
    }


def _find_preserved_xgwx() -> Path:
    configured = os.environ.get(_XGWX_ENV)
    if configured:
        candidates = [Path(configured).expanduser().resolve()]
    else:
        artifact_roots = (
            _PLATFORM_ROOT / "artifacts",
            _CHECKOUT_ROOT / "artifacts",
        )
        candidates = sorted(
            candidate.resolve()
            for root in artifact_roots
            if root.is_dir()
            for candidate in root.rglob("*.xgwx")
            if candidate.parent.name.casefold() == "source"
        )
    for candidate in candidates:
        if (
            candidate.is_file()
            and candidate.suffix.casefold() == ".xgwx"
            and _sha256_file(candidate) == _PRESERVED_XGWX_SHA256
        ):
            return candidate
    raise FileNotFoundError(
        "the SHA-bound preserved XGWX acceptance asset was not found; set "
        f"{_XGWX_ENV} to the file with SHA-256 {_PRESERVED_XGWX_SHA256}"
    )


def _find_mcp_command() -> str:
    configured = os.environ.get("CONTROLSTUDIO_MCP_COMMAND")
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_file():
            return str(configured_path.resolve())
        resolved = shutil.which(configured)
        if resolved:
            return resolved
    scripts_dir = "Scripts" if os.name == "nt" else "bin"
    executable_name = "controlstudio-plc-mcp.exe" if os.name == "nt" else "controlstudio-plc-mcp"
    sibling = _CHECKOUT_ROOT / "gidong-plc-mcp" / ".venv" / scripts_dir / executable_name
    if sibling.is_file():
        return str(sibling.resolve())
    resolved = shutil.which("controlstudio-plc-mcp")
    if resolved:
        return resolved
    raise FileNotFoundError(
        "a production controlstudio-plc-mcp executable was not found; set "
        "CONTROLSTUDIO_MCP_COMMAND or install the MCP artifact"
    )


def _unused_model_settings() -> StoredModelConnection:
    return StoredModelConnection(
        protocol=MODEL_PROTOCOL,
        base_url="https://model.invalid/v1",
        model="unused-by-tool-dispatch-test",
        timeout_seconds=1.0,
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        capability_verified_at=None,
        secrets=ModelSecrets(api_key=None, custom_headers={}),
    )


class _RecordingSupervisor:
    def __init__(self) -> None:
        self.tool_results: list[dict[str, Any]] = []

    async def send_tool_result(self, payload: Mapping[str, Any]) -> None:
        self.tool_results.append(dict(payload))


class _FailureGateway:
    """One fault-injection seam around the real broker and Agent wire mapper."""

    def __init__(self, failure_type: type[MCPToolError]) -> None:
        self._failure_type = failure_type
        self._identity = {
            "workspace_id": "11111111-1111-4111-8111-111111111111",
            "revision_id": "22222222-2222-4222-8222-222222222222",
        }

    @property
    def agent_session_identity_snapshot(self) -> dict[str, Any]:
        return dict(self._identity)

    async def execute_agent_tool(self, _name: str, _arguments: dict[str, Any]) -> Any:
        raise self._failure_type("deterministic injected failure")


class _PostflightTransportGateway:
    """Lose the MCP channel only after a static Agent tool returned."""

    def __init__(self) -> None:
        self._identity = {
            "workspace_id": "11111111-1111-4111-8111-111111111111",
            "revision_id": "22222222-2222-4222-8222-222222222222",
            "source_kind": "local_file",
            "provenance_receipt_digest": None,
            "source_sha256": "a" * 64,
            "working_path": r"C:\work\project.xgwx",
            "working_sha256": "b" * 64,
        }

    @property
    def agent_session_identity_snapshot(self) -> dict[str, Any]:
        return dict(self._identity)

    async def execute_agent_tool(self, _name: str, _arguments: dict[str, Any]) -> Any:
        return {"delivered": True}

    async def call_workspace_tool(self, _name: str, _arguments: dict[str, Any]) -> Any:
        raise MCPTransportError("deterministic postflight disconnect")


class _AsyncTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        asyncio.get_running_loop().set_debug(False)


async def _dispatch_and_capture(
    broker: MCPExecutionBroker,
    request: ExternalToolRequestV1,
) -> tuple[list[tuple[str, dict[str, Any]]], list[dict[str, Any]]]:
    supervisor = _RecordingSupervisor()
    client = SubprocessAgentClient(
        _unused_model_settings(),
        broker,
        cast(AgentSupervisor, supervisor),
    )
    events: list[tuple[str, dict[str, Any]]] = []

    async def capture(event_type: str, payload: dict[str, Any]) -> None:
        events.append((event_type, dict(payload)))

    await client._dispatch_tool_request(
        request,
        catalog={request.tool_name},
        on_tool_event=capture,
    )
    return events, supervisor.tool_results


def _request(
    *,
    request_id: str,
    arguments: dict[str, Any],
    tool_name: str = "search_workspace_evidence",
) -> ExternalToolRequestV1:
    return ExternalToolRequestV1(
        request_id=request_id,
        conversation_id="vertical-conversation",
        run_id="vertical-run",
        tool_name=tool_name,
        arguments=arguments,
    )


class FailureKindContractTests(_AsyncTestCase):
    async def test_broker_event_and_agent_wire_keep_the_same_failure_kind(self) -> None:
        cases = (
            (MCPToolRejectedError, "tool_error"),
            (MCPTransportError, "transport"),
            (MCPToolError, "host_policy"),
        )
        for failure_type, expected_kind in cases:
            with self.subTest(failure_kind=expected_kind):
                gateway = _FailureGateway(failure_type)
                broker = MCPExecutionBroker(cast(MCPGateway, gateway))
                request = _request(
                    request_id=f"failure-{expected_kind}",
                    arguments={"query": "M00217"},
                )

                events, tool_results = await _dispatch_and_capture(broker, request)

                self.assertEqual(
                    [event_type for event_type, _ in events],
                    [
                        "tool.started",
                        "tool.failed",
                    ],
                )
                self.assertEqual(events[-1][1]["failure_kind"], expected_kind)
                self.assertEqual(
                    tool_results,
                    [
                        {
                            "request_id": request.request_id,
                            "status": "failed",
                            "failure_kind": expected_kind,
                        }
                    ],
                )

    async def test_static_postflight_disconnect_remains_transport(self) -> None:
        gateway = _PostflightTransportGateway()
        broker = MCPExecutionBroker(cast(MCPGateway, gateway))
        request = _request(
            request_id="postflight-transport",
            arguments={"query": "M00217"},
        )

        events, tool_results = await _dispatch_and_capture(broker, request)

        self.assertEqual(
            [event_type for event_type, _ in events],
            ["tool.started", "tool.failed"],
        )
        self.assertEqual(events[-1][1]["failure_kind"], "transport")
        self.assertEqual(
            tool_results,
            [
                {
                    "request_id": request.request_id,
                    "status": "failed",
                    "failure_kind": "transport",
                }
            ],
        )

    async def test_workspace_gateway_exposes_connection_loss_as_transport(self) -> None:
        class DisconnectingClient:
            async def call_tool(self, _name: str, _arguments: dict[str, Any]) -> Any:
                raise ConnectionError("deterministic channel loss")

        gateway = MCPGateway(command="unused")
        gateway._client = cast(Any, DisconnectingClient())
        gateway._status = MCPStatus(status="ready")

        with self.assertRaises(MCPTransportError):
            await gateway.call_workspace_tool(
                "workspace.status",
                {"workspace_id": "11111111-1111-4111-8111-111111111111"},
            )

        self.assertEqual(gateway.status.status, "unavailable")


class WorkspaceStaticLineageTests(unittest.TestCase):
    def test_search_context_is_journaled_and_tamper_checked(self) -> None:
        match_id = "1" * 64
        query = {
            "literal": "M00217",
            "programId": None,
            "searchDigest": "2" * 64,
            "offset": 0,
            "limit": 100,
            "returned": 1,
            "total": 1,
            "nextOffset": None,
        }
        identity_binding = {
            "programId": "program-main",
            "program": "MAIN",
            "payloadSha256": "3" * 64,
        }
        payload_binding = {
            **identity_binding,
            "stableRepeat": True,
            "cacheHit": False,
        }
        logical_scope = {
            "toolName": "project.search",
            "query": query,
            "coverageUnitKeys": [match_id],
        }
        scope_material = {
            **logical_scope,
            "payloadSetDigest": _canonical_digest([identity_binding]),
            "cacheReused": False,
            "sequentialCapture": False,
            "payloadBindingsTruncated": False,
        }
        scope = {
            "scopeKey": _canonical_digest(scope_material),
            "logicalScopeKey": _canonical_digest(logical_scope),
            "query": query,
            "coverageUnitKeys": [match_id],
            "countsAdditive": True,
            "payloadCount": 1,
            "payloadSetDigest": scope_material["payloadSetDigest"],
            "payloadBindings": [payload_binding],
            "payloadBindingsTruncated": False,
            "cacheReused": False,
            "sequentialCapture": False,
        }
        receipt = {
            "schema": STRUCTURAL_ANALYSIS_EVIDENCE_SCHEMA,
            "toolName": "project.search",
            "scope": scope,
            "coverage": {
                "area": "programs",
                "status": "complete",
                "observedCount": 1,
                "totalCount": 1,
                "truncated": False,
                "limitations": [],
            },
            "receiptIndexTruncated": False,
            "receiptIndexLimitations": [],
            "locationFactCount": 0,
            "locationFactReturnedCount": 0,
            "locationFactsTruncated": False,
            "locationFactsDigest": _canonical_digest([]),
            "locationFacts": [],
            "relationFactCount": 0,
            "relationFactReturnedCount": 0,
            "relationFactsTruncated": False,
            "relationFactsDigest": _canonical_digest([]),
            "relationFacts": [],
        }
        receipt["receiptDigest"] = _canonical_digest(receipt)
        context = {
            "sourceReceiptDigest": receipt["receiptDigest"],
            "searchDigest": query["searchDigest"],
            "coverageUnitKeys": [match_id],
            "candidates": [
                {
                    "kind": "program_name",
                    "matchId": match_id,
                    "programId": "program-main",
                    "programName": "MAIN",
                }
            ],
            "candidateCount": 1,
            "truncated": False,
        }

        journaled = _agent_tool_evidence_ref(
            conversation_id="vertical-conversation",
            run_id="vertical-run",
            tool_name="workspace.search",
            tool_call_id="search-call",
            evidence_contract=receipt,
            evidence_context=context,
        )

        assert journaled is not None
        projected = project_historical_evidence(journaled)
        self.assertEqual(projected["modelContext"], context)
        tampered = copy.deepcopy(journaled)
        tampered["modelContext"]["candidates"][0]["programName"] = "OTHER"
        with self.assertRaisesRegex(
            AgentProtocolError,
            "context digest is invalid",
        ):
            project_historical_evidence(tampered)

    def test_restored_workspace_uses_wire_receipt_digest_and_rejects_drift(self) -> None:
        digest = "a" * 64
        expected = {
            "workspace_id": "11111111-1111-4111-8111-111111111111",
            "revision_id": "22222222-2222-4222-8222-222222222222",
            "source_kind": "restored_from_controller",
            "provenance_receipt_digest": digest,
            "source_sha256": "b" * 64,
            "working_path": r"C:\workspaces\restored\working.xgwx",
            "working_sha256": "c" * 64,
        }
        status_result = {
            "verified": True,
            "workspace_id": expected["workspace_id"],
            "revision_id": expected["revision_id"],
            "source_kind": expected["source_kind"],
            "source": {"sha256": expected["source_sha256"]},
            "working_copy": {
                "path": expected["working_path"],
                "current_sha256": expected["working_sha256"],
            },
            "provenance": {"receiptDigest": digest},
        }

        _validate_workspace_status_lineage(status_result, expected_identity=expected)
        status_result["provenance"]["receiptDigest"] = "d" * 64
        with self.assertRaisesRegex(MCPToolError, "workspace status binding is invalid"):
            _validate_workspace_status_lineage(status_result, expected_identity=expected)


@asynccontextmanager
async def _opened_preserved_workspace() -> AsyncIterator[
    tuple[MCPGateway, MCPExecutionBroker, dict[str, Any], Path]
]:
    source = _find_preserved_xgwx()
    source_hash = _sha256_file(source)
    command = _find_mcp_command()
    previous_workspace_root = os.environ.get("CONTROLSTUDIO_WORKSPACE_ROOT")
    with tempfile.TemporaryDirectory(prefix="controlstudio-xgwx-acceptance-") as temporary:
        os.environ["CONTROLSTUDIO_WORKSPACE_ROOT"] = str(Path(temporary) / "workspaces")
        gateway = MCPGateway(command=command, init_timeout_seconds=30.0)
        try:
            await gateway.start()
            opened = await gateway.call_workspace_tool(
                "workspace.open_native",
                {
                    "source_path": str(source),
                    "expected_source_hash": source_hash,
                },
            )
            broker = MCPExecutionBroker(gateway)
            broker.bind_agent_workspace(_workspace_static_binding(opened))
            yield gateway, broker, opened, source
        finally:
            await gateway.stop()
            if previous_workspace_root is None:
                os.environ.pop("CONTROLSTUDIO_WORKSPACE_ROOT", None)
            else:
                os.environ["CONTROLSTUDIO_WORKSPACE_ROOT"] = previous_workspace_root


@unittest.skipUnless(
    os.environ.get(_ACCEPTANCE_ENV) == "1",
    f"set {_ACCEPTANCE_ENV}=1 to run the preserved-XGWX subprocess acceptance tests",
)
class PreservedXgwxSubprocessAcceptanceTests(_AsyncTestCase):
    async def test_structure_projection_is_representative_and_model_bounded(self) -> None:
        async with _opened_preserved_workspace() as (_gateway, broker, _opened, _source):
            events: list[tuple[str, dict[str, Any]]] = []

            async def capture(event_type: str, payload: dict[str, Any]) -> None:
                events.append((event_type, dict(payload)))

            await broker.execute_external_tool_request(
                _request(
                    request_id="preserved-structure",
                    tool_name="inspect_workspace_structure",
                    arguments={},
                ),
                sink=capture,
            )

            receipt = events[-1][1]["evidence_contract"]
            facts = receipt["structureFacts"]
            encoded_facts = json.dumps(
                facts,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            self.assertLessEqual(len(encoded_facts), 49_000)
            self.assertTrue(receipt["structureFactsTruncated"])
            self.assertLess(
                receipt["structureFactReturnedCount"],
                receipt["structureFactCount"],
            )
            self.assertTrue(
                {"configuration", "task", "code_unit", "module"}.issubset(
                    {fact["kind"] for fact in facts}
                )
            )

    async def test_static_search_crosses_workspace_broker_and_evidence_contract(self) -> None:
        async with _opened_preserved_workspace() as (gateway, broker, opened, source):
            source_hash_before = _sha256_file(source)
            self.assertEqual(source_hash_before, _PRESERVED_XGWX_SHA256)
            self.assertEqual(gateway.status.status, "ready")
            self.assertGreaterEqual(gateway.status.tool_count, 70)

            catalog = await broker.agent_tool_catalog(
                conversation_id="vertical-conversation",
                run_id="vertical-run",
            )
            aliases = {definition.tool_name for definition in catalog}
            self.assertTrue(
                {
                    "compare_workspace_source",
                    "inspect_workspace_hardware_context",
                    "inspect_workspace_structure",
                    "search_workspace_evidence",
                }.issubset(aliases)
            )

            events: list[tuple[str, dict[str, Any]]] = []

            async def capture(event_type: str, payload: dict[str, Any]) -> None:
                events.append((event_type, dict(payload)))

            result = await broker.execute_external_tool_request(
                _request(request_id="preserved-search", arguments={"query": "M00217"}),
                sink=capture,
            )

            self.assertEqual(
                result["project_search"]["search"]["match_count"],
                1,
            )
            self.assertEqual(
                [event_type for event_type, _ in events],
                [
                    "tool.started",
                    "tool.completed",
                ],
            )
            receipt = events[-1][1]["evidence_contract"]
            self.assertEqual(receipt["schema"], STRUCTURAL_ANALYSIS_EVIDENCE_SCHEMA)
            self.assertEqual(receipt["toolName"], "project.search")
            context = events[-1][1]["evidence_context"]
            self.assertEqual(
                validate_project_search_context(context, receipt=receipt),
                context,
            )
            self.assertEqual(context["candidateCount"], 1)
            self.assertEqual(context["sourceReceiptDigest"], receipt["receiptDigest"])
            journaled = _agent_tool_evidence_ref(
                conversation_id="vertical-conversation",
                run_id="vertical-run",
                tool_name="workspace.search",
                tool_call_id="preserved-search-call",
                evidence_contract=receipt,
                evidence_context=context,
            )
            assert journaled is not None
            self.assertEqual(journaled["modelContext"], context)
            self.assertEqual(
                journaled["modelContextDigest"],
                hashlib.sha256(
                    json.dumps(
                        context,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest(),
            )

            no_match_events: list[tuple[str, dict[str, Any]]] = []

            async def capture_no_match(
                event_type: str,
                payload: dict[str, Any],
            ) -> None:
                no_match_events.append((event_type, dict(payload)))

            await broker.execute_external_tool_request(
                _request(
                    request_id="preserved-search-no-match",
                    arguments={"query": "CONTROLSTUDIO_LITERAL_NOT_PRESENT_9F4A"},
                ),
                sink=capture_no_match,
            )
            no_match_receipt = no_match_events[-1][1]["evidence_contract"]
            no_match_context = no_match_events[-1][1]["evidence_context"]
            self.assertEqual(no_match_context["candidateCount"], 0)
            self.assertEqual(no_match_context["candidates"], [])
            self.assertFalse(no_match_context["truncated"])
            self.assertEqual(
                validate_project_search_context(
                    no_match_context,
                    receipt=no_match_receipt,
                ),
                no_match_context,
            )

            status = await gateway.call_workspace_tool(
                "workspace.status",
                {"workspace_id": opened["workspace_id"]},
            )
            self.assertEqual(status["source"]["original_state"], "unchanged")
            self.assertEqual(status["working_copy"]["state"], "clean")
            self.assertEqual(_sha256_file(source), source_hash_before)

    async def test_stale_rejection_and_tamper_bindings_remain_distinct(self) -> None:
        async with _opened_preserved_workspace() as (gateway, broker, opened, _source):
            mismatched_request = _request(
                request_id="mismatched-host-identity",
                arguments={
                    "query": "M00217",
                    "workspace_id": "33333333-3333-4333-8333-333333333333",
                },
            )
            host_events, host_results = await _dispatch_and_capture(
                broker,
                mismatched_request,
            )
            self.assertEqual(host_events[-1][1]["failure_kind"], "host_policy")
            self.assertEqual(host_results[-1]["failure_kind"], "host_policy")
            self.assertEqual(gateway.status.status, "ready")

            stale_binding = _workspace_static_binding(opened)
            stale_binding["revision_id"] = _STALE_REVISION_ID
            broker.bind_agent_workspace(stale_binding)
            stale_request = _request(
                request_id="stale-revision",
                arguments={"query": "M00217"},
            )

            stale_events, stale_results = await _dispatch_and_capture(broker, stale_request)

            self.assertEqual(stale_events[-1][1]["failure_kind"], "tool_error")
            self.assertEqual(stale_results[-1]["failure_kind"], "tool_error")
            self.assertEqual(gateway.status.status, "ready")

            expected_identity = _workspace_static_binding(opened)
            broker.bind_agent_workspace(expected_identity)
            raw = await gateway.execute_agent_tool(
                "workspace.search",
                {"query": "M00217"},
            )
            valid_receipt = broker._evidence_contract(
                "workspace.search",
                raw,
                expected_session_identity=expected_identity,
            )
            self.assertEqual(valid_receipt["toolName"], "project.search")

            lineage_tamper = copy.deepcopy(raw)
            lineage_tamper["workspace_binding"]["source_sha256"] = "0" * 64
            _reseal_outer_workspace_search(lineage_tamper)
            with self.assertRaisesRegex(MCPToolError, "workspace search binding is invalid"):
                broker._evidence_contract(
                    "workspace.search",
                    lineage_tamper,
                    expected_session_identity=expected_identity,
                )

            outer_tamper = copy.deepcopy(raw)
            outer_tamper["receipt_digest"] = "0" * 64
            with self.assertRaisesRegex(MCPToolError, "workspace search binding is invalid"):
                broker._evidence_contract(
                    "workspace.search",
                    outer_tamper,
                    expected_session_identity=expected_identity,
                )

            inner_tamper = copy.deepcopy(raw)
            inner_tamper["project_search"]["search"]["search_digest"] = "0" * 64
            _reseal_outer_workspace_search(inner_tamper)
            with self.assertRaisesRegex(MCPToolError, "project search digest is invalid"):
                broker._evidence_contract(
                    "workspace.search",
                    inner_tamper,
                    expected_session_identity=expected_identity,
                )

            semantic_tamper = copy.deepcopy(raw)
            match = semantic_tamper["project_search"]["search"]["matches"][0]
            match["matched_text"] = f"{match['matched_text']}-tampered"
            _reseal_inner_project_search(semantic_tamper)
            _reseal_outer_workspace_search(semantic_tamper)
            with self.assertRaisesRegex(MCPToolError, "project search match identity is invalid"):
                broker._evidence_contract(
                    "workspace.search",
                    semantic_tamper,
                    expected_session_identity=expected_identity,
                )


def _reseal_inner_project_search(result: dict[str, Any]) -> None:
    search = result["project_search"]["search"]
    unsigned = {key: value for key, value in search.items() if key != "search_digest"}
    search["search_digest"] = _canonical_digest(unsigned)


def _reseal_outer_workspace_search(result: dict[str, Any]) -> None:
    result["receipt_digest"] = _canonical_digest(
        {
            "schema": result["schema"],
            "workspace_binding": result["workspace_binding"],
            "project_search": result["project_search"],
        }
    )


if __name__ == "__main__":
    unittest.main()
