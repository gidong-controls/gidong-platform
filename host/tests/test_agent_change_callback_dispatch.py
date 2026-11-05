"""Exercise the real dispatcher without a sidecar, broker, model or native input."""

import asyncio
from types import SimpleNamespace

import pytest

from controlstudio_host.agent_client import SubprocessAgentClient


def dispatch(value=None, *, bound=True, native=False, quarantine=False,
             tool_name="change.propose_offline", catalog=None):
    callback_arguments = []
    events = []
    replies = []

    async def callback(**arguments):
        callback_arguments.append(arguments)
        return value

    async def event(kind, payload):
        events.append((kind, payload))

    async def send_result(payload, *, transport_plan):
        replies.append((payload, transport_plan))

    client = SubprocessAgentClient.__new__(SubprocessAgentClient)
    client._change_plan_callback = callback if bound else None
    client._supervisor = SimpleNamespace(send_tool_result=send_result)
    request = SimpleNamespace(
        tool_name=tool_name, request_id="synthetic-request",
        tool_call_id=None, arguments={"summary": "synthetic change"},
    )
    asyncio.run(client._dispatch_tool_request(
        request, catalog=catalog or set(), design_catalog=set(),
        on_tool_event=event, native_run=native,
        quarantine_diagnostic_only=quarantine,
    ))
    return callback_arguments, events, replies


@pytest.mark.parametrize("value", [
    {"state": "PENDING"},
    {"status": "not_created", "reason": "synthetic precondition"},
    {"state": "COMPLETED"},
])
def test_bound_change_callback_returns_validated_completed_result(value):
    calls, events, replies = dispatch(value)
    assert calls == [{"summary": "synthetic change"}]
    assert [kind for kind, _ in events] == ["tool.selected", "tool.started", "tool.completed"]
    assert len(replies) == 1
    payload, plan = replies[0]
    assert payload == {"request_id": "synthetic-request", "status": "completed", "result": value}
    assert plan == [("tool.result", payload)]


@pytest.mark.parametrize(("value", "reason", "stage"), [
    ({"state": "FAILED"}, "change_execution_failed", "execution"),
    ({"state": "FAILED", "change_outcome": {
        "errorCode": "change_preflight_failed_before_mutation",
    }}, "change_preflight_failed_before_mutation", "preflight"),
    ({"state": "ROLLED_BACK"}, "change_verification_rolled_back", "verification"),
])
def test_terminal_failure_keeps_transaction_in_completed_protocol(value, reason, stage):
    calls, events, replies = dispatch(value)
    assert len(calls) == 1
    assert [kind for kind, _ in events] == ["tool.selected", "tool.started", "tool.failed"]
    assert events[-1][1]["reason_code"] == reason
    assert events[-1][1]["stage"] == stage
    assert len(replies) == 1
    payload, plan = replies[0]
    assert payload["status"] == "completed"
    assert payload["result"] == value
    assert plan == [("tool.result", payload)]


@pytest.mark.parametrize("options", [
    {"bound": False},
    {"native": True},
    {"quarantine": True},
    {"tool_name": "design.edit"},
    {"tool_name": "change.propose_other"},
    {"native": True, "catalog": {"change.propose_offline"}},
])
def test_unavailable_callback_returns_one_typed_policy_failure(options):
    calls, events, replies = dispatch({"state": "PENDING"}, **options)
    assert calls == []
    assert [kind for kind, _ in events] == ["tool.selected", "tool.failed"]
    assert events[-1][1]["failure_kind"] == "host_policy"
    assert len(replies) == 1
    payload, plan = replies[0]
    assert payload["status"] == "failed"
    assert payload["failure_kind"] == "host_policy"
    assert plan == [("tool.result", payload)]
