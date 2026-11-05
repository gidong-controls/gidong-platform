"""Synthetic Host boundary regressions; no app startup, native input or database."""

import ast
import asyncio
import contextvars
import json
import uuid
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, HTTPException, status

from controlstudio_host import app as host
from controlstudio_host.models import ChangeTransactionState
from controlstudio_host.store import TargetOperationLeaseConflict


def citation(reference):
    return {
        "knowledge_ref": reference, "source_id": "doc", "chunk_id": "chunk",
        "content": "synthetic", "title": "Document", "kind": "manual",
        "scope": "global", "authority": "user_imported_reference", "metadata": {},
        "page": 1, "section": "Section 1",
    }


@pytest.mark.parametrize("reference", ["knowledge://doc", "knowledge://doc?page=1&section=Section%201"])
def test_knowledge_citation_accepts_exact_source_with_optional_query(reference):
    result = host._agent_knowledge_hit_v1(citation(reference))
    assert result.citation.knowledge_ref == reference
    assert result.citation.source_id == "doc"
    assert result.citation.page == 1 and result.citation.section == "Section 1"


@pytest.mark.parametrize("reference", [
    "knowledge://doc-other?page=1", "knowledge://doc/child", "knowledge://doc#other",
    "knowledge://other?source=doc", "https://doc", "knowledge://DOC",
])
def test_knowledge_citation_rejects_different_source_identity(reference):
    with pytest.raises(ValueError, match="citation is not canonical"):
        host._agent_knowledge_hit_v1(citation(reference))


def close_receipt(lifetime=42.0):
    return {
        "operation": "xg5000.reviewed_no_save_close", "verified": True,
        "source_closed": True, "automatic_workspace_open": False,
        "process_terminated": False, "discardUnsavedChanges": False,
        "unsavedChangesDiscarded": False,
        "reviewed_binding": {"pid": 7, "hwnd": 8, "process_create_time": lifetime,
                             "project_hash": "a" * 64},
    }


@pytest.mark.parametrize("lifetime", [42, 42.0, 42.0009])
def test_reviewed_close_keeps_existing_lifetime_tolerance(lifetime):
    raw = close_receipt(lifetime)
    result = host._validate_reviewed_no_save_close_binding(
        raw, pid=7, hwnd=8, process_create_time=42.0,
        context={"project_hash": "a" * 64}, require_no_discard=True,
    )
    assert result == raw["reviewed_binding"]


@pytest.mark.parametrize("lifetime", [None, True, False, float("nan"), float("inf"),
                                      -float("inf"), 0, -1, "42", 42.002])
@pytest.mark.parametrize("field", ["receipt", "expected"])
def test_reviewed_close_rejects_invalid_or_different_lifetime(lifetime, field):
    raw = close_receipt(lifetime if field == "receipt" else 42.0)
    with pytest.raises(HTTPException) as rejected:
        host._validate_reviewed_no_save_close_binding(
            raw, pid=7, hwnd=8,
            process_create_time=lifetime if field == "expected" else 42.0,
        )
    assert rejected.value.status_code == 502


@pytest.mark.parametrize("change", ["pid", "hwnd", "context", "discard"])
def test_reviewed_close_preserves_target_context_and_no_discard_guards(change):
    raw = close_receipt()
    if change == "discard":
        raw["unsavedChangesDiscarded"] = True
    else:
        raw["reviewed_binding"]["project_hash" if change == "context" else change] = "other"
    with pytest.raises(HTTPException) as rejected:
        host._validate_reviewed_no_save_close_binding(
            raw, pid=7, hwnd=8, process_create_time=42.0,
            context={"project_hash": "a" * 64}, require_no_discard=True,
        )
    assert rejected.value.status_code == 502


@pytest.mark.parametrize(("pid", "hwnd", "allowed"), [
    (7, 8, True), (9, 8, False), (7, 9, False), (True, 8, False), (7, False, False),
])
def test_quarantined_reviewed_close_lease_keeps_exact_target_denial(pid, hwnd, allowed):
    # Compile the actual closure body, replacing only its surrounding app state.
    source = Path(host.__file__)
    node = next(n for n in ast.walk(ast.parse(source.read_text(encoding="utf-8")))
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "target_switch_lease")
    module = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node,
    ], type_ignores=[]))
    actions = []
    transaction = SimpleNamespace(
        id="transaction", state=ChangeTransactionState.QUARANTINED,
        target=SimpleNamespace(workspace_id="workspace", project_path="synthetic.xgwx",
                               model_dump=lambda **kwargs: {"workspace_id": "workspace"}),
    )
    checkpoint = {
        "host_transaction_id": transaction.id, "workspace_id": "workspace",
        "project_path": "synthetic.xgwx", "state": "QUARANTINED",
        "recovery_action": "rollback_required", "pid": 7, "hwnd": 8,
    }

    async def read_checkpoint(*args):
        return deepcopy(checkpoint)

    async def body():
        return {"pid": pid, "hwnd": hwnd}

    def acquire(**kwargs):
        assert kwargs["allowed_change_transaction_id"] == transaction.id
        actions.append("acquire")

    store = SimpleNamespace(
        selected_target_recovery_authority=lambda: {
            "owner_kind": "offline_change", "state": "QUARANTINED", "operation_id": transaction.id,
        },
        get_change_transaction=lambda identifier: transaction,
        reconcile_retired_owned_launch_target_lease=lambda: None,
        acquire_target_operation_lease=acquire,
        get_host_state=lambda key: None,
        park_target_operation_lease=lambda **kwargs: actions.append("park"),
    )
    namespace = {
        "uuid": uuid, "json": json, "Depends": Depends, "HTTPException": HTTPException,
        "status": status, "engineering_target_run_guard": lambda: None, "store": store,
        "ChangeTransactionState": ChangeTransactionState, "mcp": object(),
        "_reviewed_change_close_checkpoint": read_checkpoint,
        "active_workspace": {"workspace_id": "workspace"}, "_same_path": lambda a, b: a == b,
        "TargetOperationLeaseConflict": TargetOperationLeaseConflict,
        "launch_wait_request_context": contextvars.ContextVar("reviewed-close-test", default=None),
        "owned_launch_wait_requires_park": lambda value: False,
    }
    exec(compile(module, str(source), "exec"), namespace)
    request = SimpleNamespace(
        url=SimpleNamespace(path="/api/v1/xg5000/reviewed-no-save-close"),
        json=body, state=SimpleNamespace(),
    )

    async def scenario():
        lease = namespace["target_switch_lease"](request)
        if allowed:
            await anext(lease)
            assert request.state.prior_terminal_reviewed_close is False
            assert request.state.explicit_clean_reviewed_close is False
            await lease.aclose()
            assert actions == ["acquire", "park"]
        else:
            with pytest.raises(HTTPException) as rejected:
                await anext(lease)
            assert rejected.value.status_code == 409
            assert actions == []

    asyncio.run(scenario())
