"""Real restore HTTP boundary with temporary stores and no native input."""
import asyncio
import hashlib
import hmac
import json
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from controlstudio_host.api_models import ControllerRestorePreflightV1
from controlstudio_host.app import BROWSER_CSRF_PREFIX, create_app
from controlstudio_host.config import HostConfig
from controlstudio_host.knowledge import KnowledgeStore
from controlstudio_host.mcp_client import MCPGateway, MCPStartupError, MCPStatus
from controlstudio_host.store import HostStore


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()


def preflight(observed_at):
    executable = {"sha256": "a" * 64, "fileVersion": "4.82", "productVersion": "4.82", "peMachine": "x86"}
    menu = {"requestedPath": ["Project", "Open from PLC"],
        "actualPath": ["Project", "Open from PLC"], "commandId": 123, "enabled": True}
    profile = {"schema": "controlstudio-controller-restore-surface-profile/v1",
        "executable": executable, "openFromPlc": menu, "saveAs": {**menu, "commandId": 124}}
    profile["profileDigest"] = digest(profile)
    value = {"schema": "controlstudio-controller-restore-preflight/v1", "observedAt": observed_at,
        "target": {"pid": 501, "hwnd": 502, "processCreateTime": 123.0,
            "title": "XG5000", "className": "XG5000", "executable": executable},
        "surfaceProfile": profile, "visibleDialogCount": 0, "ready": True,
        "blockers": [], "readOnly": True, "invoked": False,
        "limitations": ["Surface observation does not restore a PLC project."]}
    value["receiptDigest"] = digest(value)
    return ControllerRestorePreflightV1.model_validate(value).model_dump(mode="json", by_alias=True)


class RestoreGateway(MCPGateway):
    def __init__(self, receipt):
        super().__init__()
        self.receipt, self.calls, self.override = receipt, [], None

    @property
    def status(self):
        return MCPStatus(status="ready")

    async def call_restore_tool(self, name, arguments):
        self.calls.append((name, deepcopy(arguments)))
        assert name == "controller.begin_restore_handoff"
        now = datetime.now(timezone.utc)
        value = {"schema": "controlstudio-controller-restore-handoff/v1",
            "operationId": arguments["operation_id"],
            "startedAt": now.isoformat().replace("+00:00", "Z"),
            "expiresAt": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
            "target": self.receipt["target"],
            "preflightReceiptDigest": self.receipt["receiptDigest"],
            "profileDigest": self.receipt["surfaceProfile"]["profileDigest"],
            "dialog": {"hwnd": 503, "title": "Open from PLC", "className": "#32770"},
            "invoked": True, "confirmed": False}
        if self.override:
            value.update(self.override)
        value["receiptDigest"] = digest(value)
        return {"schema_version": "1.0", "operation": name,
            "ok": True, "verified": True, "handoff": value}


def app_fixture(root, receipt):
    store = HostStore(root / "host.sqlite3")
    knowledge = KnowledgeStore(root / "knowledge.sqlite3")
    gateway = RestoreGateway(receipt)
    config = HostConfig(database_path=store.database_path,
        knowledge_database_path=root / "knowledge.sqlite3")
    app = create_app(config=config, store=store, knowledge_store=knowledge,
        mcp=gateway, model_repository=SimpleNamespace(get=lambda: None))
    token = "restore-boundary-browser"
    app.state.browser_sessions[token] = datetime.now(timezone.utc) + timedelta(hours=1)
    headers = {"Authorization": "Bearer " + token, "Origin": config.canonical_origin,
        "x-controlstudio-csrf": hmac.new(config.launch_token.encode(),
            BROWSER_CSRF_PREFIX + token.encode(), hashlib.sha256).hexdigest()}
    store.set_host_state("controller_restore_preflight", receipt)
    return app, store, gateway, headers


def post(app, headers, body):
    async def call():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1:8767", headers=headers) as client:
            return await client.post("/api/v1/controller-project-restore/begin", json=body)
    return asyncio.run(call())


@pytest.mark.parametrize("timestamp", ["2026-11-06T01:02:03.123Z",
    "2026-11-06T01:02:03.123+00:00", "2026-11-06T10:02:03.123000+09:00"])
def test_begin_forwards_stored_timestamp_without_normalization(tmp_path, timestamp):
    receipt = preflight(timestamp)
    app, store, gateway, headers = app_fixture(tmp_path, receipt)
    operation = str(uuid.uuid4())
    body = {"pid": 501, "hwnd": 502, "operationId": operation, "timeoutSeconds": 15}
    response = post(app, headers, body)
    assert response.status_code == 200, response.text
    assert gateway.calls == [("controller.begin_restore_handoff", {
        "pid": 501, "hwnd": 502, "operation_id": operation,
        "preflight_receipt_digest": receipt["receiptDigest"],
        "preflight_observed_at": timestamp,
        "profile_digest": receipt["surfaceProfile"]["profileDigest"], "timeout_seconds": 15.0})]
    assert store.get_host_state("controller_restore_handoff") == response.json()
    assert post(app, headers, body).json() == response.json()
    assert len(gateway.calls) == 1


@pytest.mark.parametrize("invalid", ["timestamp", "digest", "target", "not_ready", "override"])
def test_begin_rejects_invalid_or_browser_overridden_preflight_before_mcp(tmp_path, invalid):
    receipt = preflight("2026-11-06T01:02:03.123Z")
    body = {"pid": 501, "hwnd": 502, "operationId": str(uuid.uuid4())}
    if invalid == "timestamp":
        receipt["observedAt"] = "not-a-timestamp"
    elif invalid == "digest":
        receipt["receiptDigest"] = "f" * 64
    elif invalid == "target":
        body["pid"] = 999
    elif invalid == "not_ready":
        receipt["visibleDialogCount"] = 1
        receipt["blockers"] = ["modal_dialog_present"]
        receipt["ready"] = False
        receipt["receiptDigest"] = digest({k: v for k, v in receipt.items() if k != "receiptDigest"})
    else:
        body["preflightObservedAt"] = "2030-01-01T00:00:00Z"
    app, store, gateway, headers = app_fixture(tmp_path, receipt)
    response = post(app, headers, body)
    assert response.status_code == (422 if invalid == "override" else 409), response.text
    assert gateway.calls == []
    assert store.get_host_state("controller_restore_handoff") is None


@pytest.mark.parametrize("override", [{"operationId": str(uuid.uuid4())},
    {"preflightReceiptDigest": "f" * 64}])
def test_begin_preserves_handoff_operation_and_preflight_receipt_binding(tmp_path, override):
    receipt = preflight("2026-11-06T01:02:03Z")
    app, store, gateway, headers = app_fixture(tmp_path, receipt)
    gateway.override = override
    response = post(app, headers, {"pid": 501, "hwnd": 502, "operationId": str(uuid.uuid4())})
    assert response.status_code == 502, response.text
    assert len(gateway.calls) == 1
    assert store.get_host_state("controller_restore_handoff") is None


@pytest.mark.parametrize("mutation", [None, "missing", "optional", "wrong_type", "wrong_bound",
    "properties_list", "properties_none", "required_number", "required_none"])
def test_restore_manifest_requires_bounded_timestamp_contract(mutation):
    fields = {key: {} for key in ("pid", "hwnd", "operation_id", "preflight_receipt_digest",
        "preflight_observed_at", "profile_digest", "timeout_seconds", "schema_version")}
    fields["preflight_observed_at"] = {"type": "string", "minLength": 1,
        "maxLength": 64, "format": "date-time"}
    required = [key for key in fields if key != "schema_version"]
    if mutation == "missing":
        del fields["preflight_observed_at"]
    elif mutation == "optional":
        required.remove("preflight_observed_at")
    elif mutation == "wrong_type":
        fields["preflight_observed_at"]["type"] = "number"
    elif mutation == "wrong_bound":
        fields["preflight_observed_at"]["maxLength"] = 65
    elif mutation == "properties_list":
        fields = list(fields)
    elif mutation == "properties_none":
        fields = None
    elif mutation == "required_number":
        required = 1
    elif mutation == "required_none":
        required = None
    tool = SimpleNamespace(name="controller.begin_restore_handoff",
        inputSchema={"properties": fields, "required": required}, meta={
            "controlstudio/endpointAuthority": "host-selected-xg5000-window",
            "controlstudio/readOnly": False, "controlstudio/agentCallable": False,
            "controlstudio/canAuthorizeWrite": False})
    if mutation is None:
        MCPGateway._validate_agent_identity_contracts([tool])
    else:
        with pytest.raises(MCPStartupError, match="Host-owned restore contract changed"):
            MCPGateway._validate_agent_identity_contracts([tool])
