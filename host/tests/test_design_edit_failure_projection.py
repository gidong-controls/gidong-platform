import asyncio
import json
from datetime import UTC, datetime

from controlstudio_contracts import ExternalToolRequestV1

from controlstudio_host.agent_client import (
    SubprocessAgentClient,
    _bounded_design_failure_diagnostics,
    _design_edit_failure_code,
)
from controlstudio_host.app import _agent_tool_failure_projection, _event_frame_v1
from controlstudio_host.store import StoredEvent


class _RecordingSupervisor:
    def __init__(self):
        self.results = []

    async def send_tool_result(self, result):
        self.results.append(result)


def test_design_failure_reasons_are_distinct_and_do_not_echo_raw_detail():
    for diagnostic, expected in (
        ("edit_made_no_change", "design_edit_unchanged"),
        ("ladder_move_repeats_prior_order", "design_edit_unchanged"),
        ("repair_section_mismatch", "design_edit_scope_rejected"),
        ("schema_invalid", "design_edit_schema_invalid"),
        ("unknown_reference", "design_edit_not_applied"),
    ):
        code = _design_edit_failure_code({"diagnostics": [
            {"code": diagnostic, "message": "PRIVATE_RAW_DETAIL"},
        ]})
        assert code == expected
        projected = _agent_tool_failure_projection({"reason_code": code, "failure_kind": "tool_error"})
        assert "PRIVATE_RAW_DETAIL" not in str(projected)
        assert projected["failure_reason"]


def test_design_failure_diagnostics_are_bounded_and_remove_sensitive_values():
    diagnostics = _bounded_design_failure_diagnostics({"diagnostics": [
        {
            "code": "schema_invalid",
            "path": "/changes/0/item/value",
            "message": "Expected object; api_key=PRIVATE at C:\\private\\draft.json",
            "expectedType": "mapping",
            "received": {"entireDesign": "must never be retained"},
        },
        *(
            {"code": f"issue_{index}", "path": f"/changes/{index}", "message": "safe"}
            for index in range(1, 8)
        ),
    ]})
    assert len(diagnostics) == 4
    assert diagnostics[0] == {
        "code": "schema_invalid",
        "path": "/changes/0/item/value",
        "message": "The proposed design value does not match the required schema.",
        "expectedType": "mapping",
    }
    assert "PRIVATE" not in str(diagnostics)
    assert "entireDesign" not in str(diagnostics)


def test_default_event_frame_keeps_existing_summary_and_hides_durable_diagnostics():
    event = StoredEvent(
        sequence=9,
        conversation_id="conversation-1",
        run_id="run-1",
        event_type="tool.failed",
        payload={
            "tool_name": "design.edit",
            "request_id": "request-1",
            "tool_call_id": "call-1",
            "failure_kind": "tool_error",
            "reason_code": "design_edit_schema_invalid",
            "diagnostics": [{
                "code": "schema_invalid",
                "path": "/changes/0",
                "message": "Safe durable detail",
            }],
        },
        created_at=datetime(2026, 9, 14, tzinfo=UTC),
    )
    frame = _event_frame_v1(event)
    payload = json.loads(frame.split("data: ", 1)[1])
    assert "diagnostics" not in payload["payload"]
    assert payload["payload"]["failure_reason"] == (
        "설계 내용 형식이 맞지 않아 해당 수정을 적용하지 못했습니다."
    )
    assert payload["payload"]["request_id"] == "request-1"
    assert payload["payload"]["tool_call_id"] == "call-1"


def test_design_callback_failure_captures_only_bounded_diagnostics():
    supervisor = _RecordingSupervisor()
    client = object.__new__(SubprocessAgentClient)
    client._supervisor = supervisor

    async def execute(_tool_name, _arguments):
        return {
            "status": "not_applied",
            "diagnostics": [{
                "code": "schema_invalid",
                "path": "/changes/0/value",
                "message": "Expected object; password=PRIVATE",
                "expectedType": "mapping",
                "received": {"password": "PRIVATE", "fullDesign": {}},
            }],
        }

    client._design_tool_callback = execute
    captured = []

    async def capture(event_type, payload):
        captured.append((event_type, payload))

    request = ExternalToolRequestV1(
        request_id="request-1",
        conversation_id="conversation-1",
        run_id="run-1",
        tool_name="design.edit",
        tool_call_id="call-1",
        arguments={"changes": [{"private": "not journaled"}]},
    )
    asyncio.run(client._dispatch_tool_request(
        request,
        catalog=set(),
        design_catalog={"design.edit"},
        on_tool_event=capture,
    ))

    failure = next(payload for kind, payload in captured if kind == "tool.failed")
    assert failure["request_id"] == "request-1"
    assert failure["tool_call_id"] == "call-1"
    assert failure["reason_code"] == "design_edit_schema_invalid"
    assert failure["diagnostics"] == [{
        "code": "schema_invalid",
        "path": "/changes/0/value",
        "message": "The proposed design value does not match the required schema.",
        "expectedType": "mapping",
    }]
    assert "arguments" not in failure
    assert "received" not in str(failure)
    assert "PRIVATE" not in str(failure)
    assert supervisor.results[0]["result"]["status"] == "not_applied"
