from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from conftest import FakeMCP, authenticate_client
from fastapi.testclient import TestClient

from controlstudio_host.agent_runtime import (
    AgentRunOutput,
    ModelCapabilityResult,
)
from controlstudio_host.app import create_app
from controlstudio_host.config import HostConfig
from controlstudio_host.mcp_client import (
    AGENT_TOOL_NAMES,
    REQUIRED_PRODUCT_MCP_TOOLS,
    MCPToolError,
)
from controlstudio_host.model_connection import (
    MemorySecretStore,
    ModelConnectionRepository,
)
from controlstudio_host.store import HostStore

EXPOSED_TEST_API_KEY_VALUE = "secret-that-must-not-be-exposed"
DEFAULT_TEST_API_KEY_VALUE = "test-key"
PROVIDER_TEST_API_KEY_VALUE = "provider-a-secret"
EXISTING_TEST_API_KEY_VALUE = "existing-secret"
OTHER_HOLDER_TOKEN_VALUE = "other-holder"


class NewProjectPlanningOutput:
    """Protocol-output fixture; Platform never imports the private planner."""

    def __init__(self, **values) -> None:
        self.__dict__.update(values)


def derive_new_project_plan_artifact(plan, brief) -> dict:
    project_name = brief.get("project_name") or plan.project_name
    return {
        "schemaVersion": "1",
        "projectName": project_name,
        "status": "needs_clarification",
        "summary": plan.summary,
        "objective": plan.objective,
        "sections": [
            {
                "sectionId": "project-brief",
                "title": "project brief",
                "items": [],
            }
        ],
        "openDecisions": [
            {
                "decisionId": "decision-fixture",
                "question": "confirm missing engineering inputs",
                "status": "unknown",
                "source": "agent",
                "blocking": True,
            }
        ],
        "completeness": {
            "totalItems": 1,
            "confirmed": 0,
            "proposed": 0,
            "unknown": 1,
            "conflicting": 0,
            "notApplicable": 0,
            "blocking": 1,
        },
    }


def calculate_project_plan_digest(artifact: dict) -> str:
    return hashlib.sha256(
        json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class FakeRuntime:
    async def run(self, *, prompt, on_token, **_kwargs) -> AgentRunOutput:
        if on_token is not None:
            await on_token("분석 ")
            await on_token("완료")
        return AgentRunOutput(
            content=f"읽기 전용 응답: {prompt}",
            model="fake-model",
            input_tokens=10,
            output_tokens=5,
        )

    async def test_capabilities(self) -> ModelCapabilityResult:
        from datetime import datetime, timezone

        return ModelCapabilityResult(
            success=True,
            tested_at=datetime.now(timezone.utc),
            completion=True,
            streaming=True,
            tool_calling=True,
            tool_result_continuation=True,
            structured_output=True,
            message="ready",
        )


class ToolProgressRuntime(FakeRuntime):
    async def run(
        self,
        *,
        prompt,
        on_token,
        on_tool_event,
        **kwargs,
    ) -> AgentRunOutput:
        await on_tool_event(
            "tool.started",
            {
                "tool_name": "project.list_programs",
                "tool_call_id": "model-call-1",
                "ignored_untrusted_field": "must-not-be-persisted",
            },
        )
        await on_tool_event(
            "tool.completed",
            {
                "tool_name": "project.list_programs",
                "tool_call_id": "model-call-1",
            },
        )
        await on_tool_event(
            "tool.started",
            {
                "tool_name": "program.read_ld",
                "tool_call_id": "model-call-2",
            },
        )
        await on_tool_event(
            "tool.failed",
            {
                "tool_name": "program.read_ld",
                "tool_call_id": "model-call-2",
                "failure_kind": "tool_error",
                "error_detail": "must-not-be-persisted",
            },
        )
        return await super().run(
            prompt=prompt,
            on_token=on_token,
            **kwargs,
        )


class BlockingRuntime(FakeRuntime):
    async def run(self, **_kwargs) -> AgentRunOutput:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class ReflectedCredentialRuntime(FakeRuntime):
    def __init__(self, *credentials: str) -> None:
        self._credentials = credentials

    async def run(self, **_kwargs) -> AgentRunOutput:
        reflected = " | ".join(self._credentials)
        raise RuntimeError(f"provider echoed Authorization and headers: {reflected}")


class FailingCapabilityRuntime(FakeRuntime):
    async def test_capabilities(self) -> ModelCapabilityResult:
        return ModelCapabilityResult(
            success=False,
            tested_at=datetime.now(timezone.utc),
            completion=True,
            streaming=True,
            tool_calling=False,
            tool_result_continuation=False,
            structured_output=True,
            message="candidate-super-secret must never be returned",
        )


def _persisted_workspace_state(
    *,
    current_hash: str = "a" * 64,
) -> dict:
    baseline_hash = "a" * 64
    return {
        "workspace_id": "11111111-1111-4111-8111-111111111111",
        "revision_id": "22222222-2222-4222-8222-222222222222",
        "source_kind": "local_file",
        "vendor": "ls",
        "engineering_system": "XG5000",
        "created_at": "2026-07-28T12:00:00+00:00",
        "source": {
            "original_path": r"C:\projects\machine.xgwx",
            "captured_path": r"C:\workspaces\source\machine.xgwx",
            "sha256": baseline_hash,
            "original_state": "unchanged",
            "original_current_sha256": baseline_hash,
        },
        "working_copy": {
            "path": r"C:\workspaces\working\machine.xgwx",
            "baseline_sha256": baseline_hash,
            "current_sha256": current_hash,
            "state": "clean" if current_hash == baseline_hash else "modified",
        },
    }


def _workspace_status_result(workspace: dict) -> dict:
    return {
        "ok": True,
        "verified": True,
        **workspace,
    }


def _browser_csrf_headers(client: TestClient) -> dict[str, str]:
    bootstrap = client.get("/api/v1/bootstrap")
    assert bootstrap.status_code == 200
    token = bootstrap.json()["csrfToken"]
    assert isinstance(token, str) and token
    return {"X-ControlStudio-CSRF": token}


def test_new_project_planning_records_workflow_without_engineering_target_or_mcp(
    tmp_path: Path,
) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    repository = ModelConnectionRepository(store, MemorySecretStore())
    repository.save(
        base_url="https://models.example/v1",
        model="fake-model",
        timeout_seconds=30,
        api_key=EXPOSED_TEST_API_KEY_VALUE,
        custom_headers={"X-Private": "private-header"},
        capability_verified_at=datetime.now(timezone.utc),
    )
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
        event_poll_interval_seconds=0.01,
    )
    captured: dict = {}

    class PlanningRuntime(FakeRuntime):
        def bind_knowledge_snapshot(self, callback) -> None:
            self._knowledge_snapshot = callback
            captured["knowledge_snapshot_bound"] = True

        def bind_change_planner(self, _callback) -> None:
            captured["change_planner_bound"] = True

        async def run(self, **kwargs) -> AgentRunOutput:
            captured.update(kwargs)
            captured["knowledge_sources"] = await self._knowledge_snapshot()
            plan = NewProjectPlanningOutput(
                status="needs_clarification",
                project_name="포장기",
                summary="포장기 계획 초안",
                objective="제품을 검사하고 자동으로 포장한다.",
                user_requirements=["검사 후 자동 포장"],
                open_questions=["사용할 센서 형식을 확정해 주세요."],
            )
            artifact = derive_new_project_plan_artifact(
                plan,
                kwargs["trusted_context"]["project_brief"],
            )
            return AgentRunOutput(
                content="# 포장기 설계 계획\n\n계획 초안",
                model="fake-model",
                input_tokens=20,
                output_tokens=10,
                workflow="new_project_planning",
                plan_status="needs_clarification",
                project_name="포장기",
                plan_artifact=artifact,
                plan_digest=calculate_project_plan_digest(artifact),
            )

    mcp = FakeMCP()
    app = create_app(
        config,
        store=store,
        mcp=mcp,
        model_repository=repository,
        runtime_factory=lambda _settings, _mcp: PlanningRuntime(),
    )

    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        # Planning is model-only and remains available even if the MCP process
        # is unavailable and no workspace/engineering session exists.
        mcp.status = type(mcp.status)(status="unavailable", error="offline")
        created = client.post(
            "/api/v1/conversations",
            json={
                "title": "신규 설계",
                "workflow": "new_project_planning",
                "projectBrief": {
                    "projectName": "포장기",
                    "machinePurpose": "제품을 자동으로 포장한다",
                    "sequence": "투입, 포장, 배출",
                    "hardwareAndIo": "",
                    "safetyAndInterlocks": "도어 개방 시 위험 출력 차단",
                },
            },
        )
        assert created.status_code == 201
        assert created.json()["workflow"] == "new_project_planning"
        assert created.json()["projectBrief"]["projectName"] == "포장기"
        conversation_id = created.json()["conversationId"]
        listed = client.get("/api/v1/conversations?limit=1&offset=0")
        assert listed.status_code == 200
        assert listed.json() == {
            "items": [
                {
                    "conversationId": conversation_id,
                    "title": "신규 설계",
                    "workflow": "new_project_planning",
                    "engineeringTarget": None,
                    "updatedAt": created.json()["updatedAt"],
                }
            ],
            "total": 1,
            "limit": 1,
            "offset": 0,
        }
        body = {
            "content": "자유 텍스트에서는 다른 설비를 설계해 줘",
            "operationId": "new-project-plan-0001",
            "workflow": "new_project_planning",
            "projectBrief": {
                "projectName": "포장기",
                "machinePurpose": "제품을 검사한 뒤 자동으로 포장한다",
                "sequence": "투입, 검사, 포장, 배출",
                "hardwareAndIo": "",
                "safetyAndInterlocks": "도어 개방 시 위험 출력 차단",
            },
        }
        submitted = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json=body,
            headers={"Idempotency-Key": body["operationId"]},
        )
        assert submitted.status_code == 202
        status_payload = client.get("/api/v1/status").json()
        assert status_payload["agent"]["state"] == "ready"
        assert status_payload["agent"]["planningReady"] is True
        assert status_payload["agent"]["engineeringReady"] is False
        feature_readiness = {
            item["featureId"]: item
            for item in status_payload["featureReadiness"]
        }
        assert feature_readiness["new_project_planning"] == {
            "featureId": "new_project_planning",
            "label": "새 프로젝트 요구사항·계획",
            "maturity": "supported",
            "verification": "real_runtime",
            "currentReady": True,
            "detail": (
                "설비 요구사항을 구조화하고 검토 가능한 계획을 만드는 "
                "Pydantic AI 경로입니다. 네이티브 로직 생성과는 별도입니다."
            ),
        }
        assert feature_readiness["native_project_create"]["maturity"] == "preview"
        assert feature_readiness["native_project_create"]["verification"] == "fake_only"
        assert feature_readiness["native_project_create"]["currentReady"] is False
        assert feature_readiness["online_observe"]["maturity"] == "preview"
        assert feature_readiness["online_observe"]["verification"] == "fake_only"
        assert feature_readiness["online_observe"]["currentReady"] is False
        assert feature_readiness["program_focus"]["maturity"] == "supported"
        assert feature_readiness["program_focus"]["verification"] == "real_xg5000"
        assert feature_readiness["program_focus"]["currentReady"] is False
        assert "online_observe" not in status_payload["capabilities"]

        deadline = time.monotonic() + 2
        detail = None
        while time.monotonic() < deadline:
            detail = client.get(f"/api/v1/conversations/{conversation_id}").json()
            if len(detail["messages"]) == 2:
                break
            time.sleep(0.01)
        legacy_detail = client.get(
            f"/api/conversations/{conversation_id}"
        ).json()
        workflow_flip = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={
                "content": "이 대화를 엔지니어링으로 바꿔 줘",
                "operationId": "new-project-plan-flip-0002",
                "workflow": "engineering",
            },
            headers={"Idempotency-Key": "new-project-plan-flip-0002"},
        )
        assert workflow_flip.status_code == 409
        assert "workflow" in workflow_flip.json()["detail"]

    assert detail is not None
    assert captured["trusted_context"] == {
        "workflow": "new_project_planning",
        "project_brief": {
            "project_name": "포장기",
            "machine_purpose": "제품을 검사한 뒤 자동으로 포장한다",
            "sequence": "투입, 검사, 포장, 배출",
            "hardware_and_io": "",
            "safety_and_interlocks": "도어 개방 시 위험 출력 차단",
            "operating_modes": "",
            "performance_and_acceptance": "",
        },
        "current_prompt_is_structured_brief": True,
    }
    assert "프로젝트 이름: 포장기" in captured["prompt"]
    assert "제품을 검사한 뒤 자동으로 포장한다" in captured["prompt"]
    assert "자유 텍스트에서는 다른 설비" not in captured["prompt"]
    assert "change_planner_bound" not in captured
    assert captured["knowledge_snapshot_bound"] is True
    assert captured["knowledge_sources"]
    assert all(
        item["vendor"] == "generic" and item["kind"] == "control_pattern"
        for item in captured["knowledge_sources"]
    )
    assert detail["messages"][0]["workflow"] == "new_project_planning"
    assert detail["messages"][0]["operationId"] == "new-project-plan-0001"
    assert "자유 텍스트에서는 다른 설비" not in detail["messages"][0]["content"]
    assert detail["messages"][1]["workflow"] == "new_project_planning"
    assert detail["messages"][1]["planStatus"] == "needs_clarification"
    assert detail["messages"][1]["projectName"] == "포장기"
    assert detail["messages"][1]["planArtifact"]["schemaVersion"] == "1"
    assert len(detail["messages"][1]["planDigest"]) == 64
    persisted_metadata = store.get_conversation(conversation_id).messages[-1].metadata
    assert persisted_metadata["plan_artifact"]["schemaVersion"] == "1"
    assert persisted_metadata["plan_digest"] == detail["messages"][1]["planDigest"]
    assert (
        detail["projectBrief"]["machinePurpose"]
        == "제품을 검사한 뒤 자동으로 포장한다"
    )
    exposed = f"{detail!r}\n{legacy_detail!r}"
    assert "models.example" not in exposed
    assert "secret-that-must-not-be-exposed" not in exposed
    assert "private-header" not in exposed


def test_conversation_rejects_second_active_run(tmp_path: Path) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    repository = ModelConnectionRepository(store, MemorySecretStore())
    repository.save(
        base_url="http://localhost:4000/v1",
        model="fake-model",
        timeout_seconds=30,
        api_key=DEFAULT_TEST_API_KEY_VALUE,
        custom_headers={},
        capability_verified_at=datetime.now(timezone.utc),
    )
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
        event_poll_interval_seconds=0.01,
    )
    app = create_app(
        config,
        store=store,
        mcp=FakeMCP(),
        model_repository=repository,
        runtime_factory=lambda _settings, _mcp: BlockingRuntime(),
    )

    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        created = client.post(
            "/api/v1/conversations",
            json={"title": "동시 실행 차단", "workflow": "engineering"},
        )
        conversation_id = created.json()["conversationId"]
        rejected_brief = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={
                "content": "브리프를 잘못 전달",
                "operationId": "engineering-brief-reject-0001",
                "workflow": "engineering",
                "projectBrief": {
                    "projectName": "잘못된 브리프",
                    "machinePurpose": "허용되면 안 됨",
                },
            },
            headers={"Idempotency-Key": "engineering-brief-reject-0001"},
        )
        assert rejected_brief.status_code == 422
        first = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={
                "content": "첫 분석",
                "operationId": "single-active-run-0001",
                "workflow": "engineering",
            },
            headers={"Idempotency-Key": "single-active-run-0001"},
        )
        assert first.status_code == 202
        second = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={
                "content": "두 번째 분석",
                "operationId": "single-active-run-0002",
                "workflow": "engineering",
            },
            headers={"Idempotency-Key": "single-active-run-0002"},
        )
        assert second.status_code == 409
        assert "active run" in second.json()["detail"]
        other = client.post(
            "/api/v1/conversations",
            json={"title": "별도 대화", "workflow": "engineering"},
        )
        assert other.status_code == 201
        other_rejected = client.post(
            f"/api/v1/conversations/{other.json()['conversationId']}/messages",
            json={
                "content": "다른 대화의 동시 분석",
                "operationId": "single-active-run-0003",
                "workflow": "engineering",
            },
            headers={"Idempotency-Key": "single-active-run-0003"},
        )
        assert other_rejected.status_code == 409
        assert first.json()["runId"] in other_rejected.json()["detail"]
        cancelled = client.post(f"/api/v1/runs/{first.json()['runId']}/cancel")
        assert cancelled.status_code == 200


def test_v1_read_only_agent_path_is_idempotent_and_replayable(
    tmp_path: Path,
) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    repository = ModelConnectionRepository(store, MemorySecretStore())
    repository.save(
        base_url="http://localhost:4000/v1",
        model="fake-model",
        timeout_seconds=30,
        api_key=DEFAULT_TEST_API_KEY_VALUE,
        custom_headers={},
        capability_verified_at=datetime.now(timezone.utc),
    )
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
        event_poll_interval_seconds=0.01,
    )
    mcp = FakeMCP()
    app = create_app(
        config,
        store=store,
        mcp=mcp,
        model_repository=repository,
        runtime_factory=lambda _settings, _mcp: ToolProgressRuntime(),
    )

    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        bootstrap = client.get("/api/v1/bootstrap")
        assert bootstrap.status_code == 200
        assert bootstrap.json()["status"]["agent"]["state"] == "ready"
        assert (
            bootstrap.json()["status"]["mcp"]["toolCount"]
            >= len(REQUIRED_PRODUCT_MCP_TOOLS)
        )
        assert (
            bootstrap.json()["status"]["mcp"]["agentToolCount"]
            == len(AGENT_TOOL_NAMES)
        )

        created = client.post(
            "/api/v1/conversations",
            json={"title": "설비 분석"},
        )
        conversation_id = created.json()["conversationId"]
        body = {
            "content": "느린 원인을 찾아줘",
            "operationId": "operation-test-0001",
        }
        first = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json=body,
            headers={"Idempotency-Key": body["operationId"]},
        )
        assert first.status_code == 202

        deadline = time.monotonic() + 2
        detail = None
        while time.monotonic() < deadline:
            detail = client.get(f"/api/v1/conversations/{conversation_id}").json()
            if len(detail["messages"]) == 2:
                break
            time.sleep(0.01)
        assert detail is not None
        assert [message["role"] for message in detail["messages"]] == [
            "user",
            "assistant",
        ]
        assert detail["messages"][1]["content"].startswith("읽기 전용 응답")

        duplicate = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json=body,
            headers={"Idempotency-Key": body["operationId"]},
        )
        assert duplicate.status_code == 202
        assert duplicate.json()["runId"] == first.json()["runId"]
        detail = client.get(f"/api/v1/conversations/{conversation_id}").json()
        assert len(detail["messages"]) == 2

        replay = client.get("/api/v1/events", params={"follow": "false"})
        assert replay.status_code == 200
        assert '"eventType":"agent.token"' in replay.text
        assert '"eventType":"agent.message"' in replay.text
        assert '"eventType":"tool.started"' in replay.text
        assert '"eventType":"tool.completed"' in replay.text
        assert '"eventType":"tool.failed"' in replay.text
        assert '"eventType":"evidence.added"' in replay.text

        tool_events = [
            event
            for event in store.list_events(conversation_id=conversation_id)
            if event.event_type.startswith("tool.")
        ]
        assert [
            (event.event_type, event.conversation_id, event.run_id, event.payload)
            for event in tool_events
        ] == [
            (
                "tool.started",
                conversation_id,
                first.json()["runId"],
                {
                    "tool_name": "project.list_programs",
                    "tool_call_id": "model-call-1",
                },
            ),
            (
                "tool.completed",
                conversation_id,
                first.json()["runId"],
                {
                    "tool_name": "project.list_programs",
                    "tool_call_id": "model-call-1",
                },
            ),
            (
                "tool.started",
                conversation_id,
                first.json()["runId"],
                {
                    "tool_name": "program.read_ld",
                    "tool_call_id": "model-call-2",
                },
            ),
            (
                "tool.failed",
                conversation_id,
                first.json()["runId"],
                {
                    "tool_name": "program.read_ld",
                    "tool_call_id": "model-call-2",
                    "failure_kind": "tool_error",
                },
            ),
        ]
        evidence_events = [
            event
            for event in store.list_events(conversation_id=conversation_id)
            if event.event_type == "evidence.added"
        ]
        assert len(evidence_events) == 1
        assert evidence_events[0].run_id == first.json()["runId"]
        assert evidence_events[0].payload == {
            "evidenceId": evidence_events[0].payload["evidenceId"],
            "title": "프로그램 목록",
            "kind": "program_location",
            "summary": (
                "현재 대상 XG5000 프로젝트에서 프로그램 목록을 읽었습니다."
            ),
            "confidence": "confirmed",
            "toolName": "project.list_programs",
        }

        instances = client.get("/api/v1/xg5000/instances")
        assert instances.status_code == 200
        assert instances.json() == {"status": "ready", "instances": []}

        attached = client.post(
            "/api/v1/xg5000/attach",
            json={"pid": 101, "hwnd": 202},
        )
        assert attached.status_code == 200
        status_payload = client.get("/api/v1/status").json()
        assert status_payload["engineeringSystem"]["state"] == "degraded"
        assert status_payload["engineeringSystem"]["sessionVerified"] is False
        assert status_payload["engineeringSystem"]["readReady"] is False
        assert status_payload["workspace"]["workspaceId"] == "session-test"
        assert mcp.bound_session is None


def test_provider_error_cannot_persist_or_log_reflected_credentials(
    tmp_path: Path,
    caplog,
) -> None:
    api_key_sentinel = "MODEL_API_CREDENTIAL_SENTINEL_DO_NOT_STORE"
    header_sentinel = "MODEL_HEADER_CREDENTIAL_SENTINEL_DO_NOT_STORE"
    store = HostStore(tmp_path / "host.sqlite3")
    repository = ModelConnectionRepository(store, MemorySecretStore())
    repository.save(
        base_url="https://models.example/v1",
        model="fake-model",
        timeout_seconds=30,
        api_key=api_key_sentinel,
        custom_headers={"X-Tenant-Secret": header_sentinel},
        capability_verified_at=datetime.now(timezone.utc),
    )
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
        event_poll_interval_seconds=0.01,
    )
    app = create_app(
        config,
        store=store,
        mcp=FakeMCP(),
        model_repository=repository,
        runtime_factory=lambda settings, _mcp: ReflectedCredentialRuntime(
            settings.secrets.api_key or "",
            *settings.secrets.custom_headers.values(),
        ),
    )
    caplog.set_level("ERROR", logger="controlstudio_host.app")

    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        created = client.post(
            "/api/v1/conversations",
            json={"title": "credential reflection"},
        )
        conversation_id = created.json()["conversationId"]
        submitted = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={
                "content": "run provider request",
                "operationId": "credential-reflection-test-0001",
            },
            headers={"Idempotency-Key": "credential-reflection-test-0001"},
        )
        assert submitted.status_code == 202

        deadline = time.monotonic() + 2
        stored_detail = None
        while time.monotonic() < deadline:
            stored_detail = store.get_conversation(conversation_id)
            if stored_detail and stored_detail.runs[0].state.value == "FAILED":
                break
            time.sleep(0.01)
        assert stored_detail is not None
        assert stored_detail.runs[0].state.value == "FAILED"

        legacy_response = client.get(f"/api/conversations/{conversation_id}")
        event_response = client.get("/api/events", params={"follow": "false"})

    exposed_text = "\n".join(
        (
            caplog.text,
            stored_detail.runs[0].error_detail or "",
            legacy_response.text,
            event_response.text,
        )
    )
    assert api_key_sentinel not in exposed_text
    assert header_sentinel not in exposed_text
    assert "provider detail was suppressed" in stored_detail.runs[0].error_detail


def test_only_verified_project_identity_is_bound_to_agent_tools(tmp_path: Path) -> None:
    class VerifiedMCP(FakeMCP):
        async def call_read_tool(self, name: str, arguments: dict) -> dict:
            result = await super().call_read_tool(name, arguments)
            if name == "xg5000.attach":
                result["metadata"][
                    "project_identity_verified_against_selected_pid"
                ] = True
                result["metadata"]["project_identity_assurance"] = "strong_readback"
            return result

    store = HostStore(tmp_path / "host.sqlite3")
    mcp = VerifiedMCP()
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
    )
    app = create_app(config, store=store, mcp=mcp)

    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        attached = client.post(
            "/api/v1/xg5000/attach",
            json={"pid": 101, "hwnd": 202},
        )
        status_payload = client.get("/api/v1/status").json()

    assert attached.status_code == 200
    assert status_payload["engineeringSystem"]["state"] == "ready"
    assert status_payload["engineeringSystem"]["sessionVerified"] is True
    assert status_payload["engineeringSystem"]["readReady"] is True
    assert (
        status_payload["engineeringSystem"]["identityAssurance"]
        == "strong_readback"
    )
    assert mcp.bound_session["pid"] == 101


def test_model_settings_api_requires_external_consent_and_probes(
    tmp_path: Path,
) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    repository = ModelConnectionRepository(store, MemorySecretStore())
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
    )
    app = create_app(
        config,
        store=store,
        mcp=FakeMCP(),
        model_repository=repository,
        runtime_factory=lambda _settings, _mcp: FakeRuntime(),
    )
    external = {
        "protocol": "openai_chat_completions",
        "baseUrl": "https://models.example/v1",
        "model": "gateway-model",
        "apiKey": "secret",
        "customHeaders": {"X-Tenant": "secret-tenant"},
        "timeoutSeconds": 30,
    }

    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        refused = client.put("/api/v1/settings/model", json=external)
        assert refused.status_code == 422
        assert "external_model_consent" in refused.json()["detail"]

        external["externalModelConsent"] = True
        saved = client.put("/api/v1/settings/model", json=external)
        assert saved.status_code == 200
        assert saved.json()["hasApiKey"] is True
        assert saved.json()["customHeaderNames"] == ["X-Tenant"]
        assert "secret" not in saved.text
        assert repository.get().capability_verified_at is not None
        assert client.get("/api/v1/status").json()["agent"]["state"] == "ready"

        probe = client.post("/api/v1/settings/model/test", json=external)
        assert probe.status_code == 200
        assert probe.json()["success"] is True
        assert all(probe.json()["capabilities"].values())


def test_model_settings_api_requires_separate_consent_for_tailscale_http(
    tmp_path: Path,
) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    repository = ModelConnectionRepository(store, MemorySecretStore())
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
    )
    app = create_app(
        config,
        store=store,
        mcp=FakeMCP(),
        model_repository=repository,
        runtime_factory=lambda _settings, _mcp: FakeRuntime(),
    )
    candidate = {
        "protocol": "openai_chat_completions",
        "baseUrl": "http://100.64.0.10:8080/v1",
        "model": "gateway-model",
        "apiKey": "secret",
        "timeoutSeconds": 30,
        "externalModelConsent": True,
    }

    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        refused = client.put("/api/v1/settings/model", json=candidate)
        assert refused.status_code == 422
        assert "insecure_http_consent" in refused.json()["detail"]

        candidate["insecureHttpConsent"] = True
        saved = client.put("/api/v1/settings/model", json=candidate)

    assert saved.status_code == 200
    assert repository.get().base_url == "http://100.64.0.10:8080/v1"


def test_capability_probe_never_reuses_secrets_after_endpoint_change(
    tmp_path: Path,
) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    repository = ModelConnectionRepository(store, MemorySecretStore())
    repository.save(
        base_url="https://provider-a.example/v1",
        model="model-a",
        timeout_seconds=30,
        api_key=PROVIDER_TEST_API_KEY_VALUE,
        custom_headers={"X-Tenant": "tenant-a"},
    )
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
    )
    captured_settings = []

    def runtime_factory(settings, _mcp):
        captured_settings.append(settings)
        return FakeRuntime()

    app = create_app(
        config,
        store=store,
        mcp=FakeMCP(),
        model_repository=repository,
        runtime_factory=runtime_factory,
    )
    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        response = client.post(
            "/api/v1/settings/model/test",
            json={
                "protocol": "openai_chat_completions",
                "baseUrl": "https://provider-b.example/v1",
                "model": "model-b",
                "timeoutSeconds": 30,
                "externalModelConsent": True,
            },
        )

    assert response.status_code == 200
    assert captured_settings[0].secrets.api_key is None
    assert captured_settings[0].secrets.custom_headers == {}


def test_failed_save_probe_preserves_prior_verified_setting_and_secrets(
    tmp_path: Path,
) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    secrets = MemorySecretStore()
    repository = ModelConnectionRepository(store, secrets)
    previous = repository.save(
        base_url="https://provider-a.example/v1",
        model="verified-model",
        timeout_seconds=30,
        api_key=EXISTING_TEST_API_KEY_VALUE,
        custom_headers={"X-Tenant": "existing-tenant"},
        capability_verified_at=datetime.now(timezone.utc),
    )
    secret_snapshot = dict(secrets.values)
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
    )
    app = create_app(
        config,
        store=store,
        mcp=FakeMCP(),
        model_repository=repository,
        runtime_factory=lambda _settings, _mcp: FailingCapabilityRuntime(),
    )
    candidate = {
        "protocol": "openai_chat_completions",
        "baseUrl": "https://provider-b.example/v1",
        "model": "unsupported-model",
        "apiKey": "candidate-super-secret",
        "customHeaders": {"X-Candidate": "candidate-private-header"},
        "timeoutSeconds": 30,
        "externalModelConsent": True,
    }

    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        response = client.put("/api/v1/settings/model", json=candidate)
        status_payload = client.get("/api/v1/status").json()

    assert response.status_code == 422
    assert "candidate-super-secret" not in response.text
    assert "candidate-private-header" not in response.text
    assert repository.get() == previous
    assert secrets.values == secret_snapshot
    assert status_payload["agent"]["state"] == "ready"
    assert status_payload["agent"]["modelDisplayName"] == "verified-model"


def test_unverified_persisted_model_setting_does_not_mark_agent_ready(
    tmp_path: Path,
) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    repository = ModelConnectionRepository(store, MemorySecretStore())
    repository.save(
        base_url="http://localhost:4000/v1",
        model="legacy-unverified-model",
        timeout_seconds=30,
        api_key=None,
        custom_headers={},
    )
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
    )
    app = create_app(
        config,
        store=store,
        mcp=FakeMCP(),
        model_repository=repository,
        runtime_factory=lambda _settings, _mcp: FakeRuntime(),
    )

    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        status_payload = client.get("/api/v1/status").json()

    assert status_payload["agent"]["state"] == "unavailable"
    assert "기능 검사" in status_payload["agent"]["detail"]


def test_local_project_creates_persistent_workspace_and_attaches_working_copy(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "host.sqlite3"
    config = HostConfig(
        database_path=database_path,
        static_dir=tmp_path / "missing-dist",
    )
    source_path = r"C:\projects\machine.xgwx"

    with TestClient(
        create_app(config, mcp=FakeMCP()),
        base_url="http://localhost:8765",
    ) as client:
        authenticate_client(client, config)
        opened = client.post(
            "/api/v1/workspaces/open-local",
            json={
                "sourcePath": source_path,
                "openInXg5000": True,
                "timeoutSeconds": 10,
            },
        )
        assert opened.status_code == 200
        payload = opened.json()
        workspace_id = payload["workspace"]["workspaceId"]
        assert workspace_id == "11111111-1111-4111-8111-111111111111"
        assert payload["workspace"]["source"]["originalPath"] == source_path
        assert payload["workspace"]["workingCopy"]["state"] == "clean"
        assert payload["launch"]["windowDetected"] is True
        assert (
            payload["launch"]["identityAssurance"]
            == "controlled_launch_read_only"
        )
        assert (
            payload["launch"]["projectIdentityReadOnlyBoundToSelectedPid"]
            is True
        )
        assert payload["session"]["projectPath"].endswith("machine.xgwx")
        assert payload["session"]["recovery"] is None
        assert (
            payload["session"]["metadata"][
                "project_identity_read_only_bound_to_selected_pid"
            ]
            is True
        )

        status_payload = client.get("/api/v1/status").json()
        assert status_payload["workspace"]["workspaceId"] == workspace_id
        assert status_payload["engineeringSystem"]["state"] == "ready"
        assert status_payload["engineeringSystem"]["sessionVerified"] is False
        assert status_payload["engineeringSystem"]["readReady"] is True
        assert (
            status_payload["engineeringSystem"]["identityAssurance"]
            == "controlled_launch_read_only"
        )
        assert "workspace_write" in status_payload["capabilities"]
        assert "open_local_project" in status_payload["capabilities"]

        checked = client.get(f"/api/v1/workspaces/{workspace_id}")
        assert checked.status_code == 200
        assert checked.json()["workingCopy"]["currentSha256"] == "a" * 64

    with TestClient(
        create_app(config, mcp=FakeMCP()),
        base_url="http://localhost:8765",
    ) as restarted:
        authenticate_client(restarted, config)
        restored_status = restarted.get("/api/v1/status").json()
        assert restored_status["workspace"]["workspaceId"] == workspace_id
        assert restored_status["engineeringSystem"]["state"] == "degraded"


def test_engineering_conversation_is_bound_to_the_exact_live_target(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "host.sqlite3"
    config = HostConfig(
        database_path=database_path,
        static_dir=tmp_path / "missing-dist",
    )
    app = create_app(config, mcp=FakeMCP())

    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        unbound = client.post(
            "/api/v1/conversations",
            json={"title": "대상 선택 전 대화"},
        )
        assert unbound.status_code == 201
        assert unbound.json()["engineeringTarget"] is None

        opened = client.post(
            "/api/v1/workspaces/open-local",
            json={
                "sourcePath": r"C:\projects\machine.xgwx",
                "openInXg5000": True,
                "timeoutSeconds": 10,
            },
        )
        assert opened.status_code == 200
        bound = client.post(
            "/api/v1/conversations",
            json={"title": "현재 설비 분석"},
        )
        assert bound.status_code == 201
        assert bound.json()["engineeringTarget"] == {
            "workspaceId": "11111111-1111-4111-8111-111111111111",
            "revisionId": "22222222-2222-4222-8222-222222222222",
            "engineeringSessionId": "session-test",
        }
        listed = client.get("/api/v1/conversations?limit=2&offset=0")
        assert listed.status_code == 200
        assert listed.json()["items"][0]["engineeringTarget"] == bound.json()[
            "engineeringTarget"
        ]

        rejected = client.post(
            f"/api/v1/conversations/{unbound.json()['conversationId']}/messages",
            json={
                "content": "현재 설비를 분석해 줘",
                "operationId": "target-mismatch-0001",
                "workflow": "engineering",
            },
            headers={"Idempotency-Key": "target-mismatch-0001"},
        )
        assert rejected.status_code == 409
        assert "현재 대상이 다릅니다" in rejected.json()["detail"]

    # A persisted workspace alone is not a live engineering session. A Host
    # restart therefore cannot silently reuse the previous target-bound chat.
    with TestClient(
        create_app(config, mcp=FakeMCP()),
        base_url="http://localhost:8765",
    ) as restarted:
        authenticate_client(restarted, config)
        rejected_after_restart = restarted.post(
            f"/api/v1/conversations/{bound.json()['conversationId']}/messages",
            json={
                "content": "재연결 없이 분석을 계속해 줘",
                "operationId": "target-mismatch-0002",
                "workflow": "engineering",
            },
            headers={"Idempotency-Key": "target-mismatch-0002"},
        )
        assert rejected_after_restart.status_code == 409
        assert "현재 대상이 다릅니다" in rejected_after_restart.json()["detail"]


def test_persisted_workspace_resume_reopens_and_rebinds_exact_identity(
    tmp_path: Path,
) -> None:
    class RecordingResumeMCP(FakeMCP):
        def __init__(self, workspace: dict) -> None:
            super().__init__()
            self.workspace = workspace
            self.workspace_calls: list[tuple[str, dict]] = []
            self.read_calls: list[tuple[str, dict]] = []

        async def call_workspace_tool(self, name: str, arguments: dict) -> dict:
            self.workspace_calls.append((name, dict(arguments)))
            if name == "workspace.status":
                return _workspace_status_result(self.workspace)
            return await super().call_workspace_tool(name, arguments)

        async def call_read_tool(self, name: str, arguments: dict) -> dict:
            self.read_calls.append((name, dict(arguments)))
            return await super().call_read_tool(name, arguments)

    database_path = tmp_path / "host.sqlite3"
    store = HostStore(database_path)
    workspace = _persisted_workspace_state()
    store.set_host_state("active_workspace", workspace)
    mcp = RecordingResumeMCP(workspace)
    config = HostConfig(
        database_path=database_path,
        static_dir=tmp_path / "missing-dist",
    )

    with TestClient(
        create_app(config, store=store, mcp=mcp),
        base_url="http://localhost:8765",
    ) as client:
        authenticate_client(client, config)
        missing_csrf = client.post(
            f"/api/v1/workspaces/{workspace['workspace_id']}/resume",
            json={"timeoutSeconds": 17},
        )
        resumed = client.post(
            f"/api/v1/workspaces/{workspace['workspace_id']}/resume",
            json={"timeoutSeconds": 17},
            headers=_browser_csrf_headers(client),
        )
        status_payload = client.get("/api/v1/status").json()

    assert missing_csrf.status_code == 403
    assert resumed.status_code == 200
    payload = resumed.json()
    assert payload["workspace"]["workspaceId"] == workspace["workspace_id"]
    assert payload["launch"]["workspaceId"] == workspace["workspace_id"]
    assert payload["session"]["projectPath"] == workspace["working_copy"]["path"]
    assert payload["session"]["projectHash"] == workspace["working_copy"]["current_sha256"]
    assert status_payload["engineeringSystem"]["state"] == "ready"
    assert status_payload["engineeringSystem"]["readReady"] is True
    assert mcp.workspace_calls == [
        ("workspace.status", {"workspace_id": workspace["workspace_id"]}),
        (
            "xg5000.open_project",
            {
                "workspace_id": workspace["workspace_id"],
                "expected_working_hash": workspace["working_copy"]["current_sha256"],
                "timeout_seconds": 17.0,
            },
        ),
    ]
    assert mcp.read_calls == [
        (
            "xg5000.attach",
            {
                "pid": 1234,
                "hwnd": 5678,
                "project_path": workspace["working_copy"]["path"],
                "project_hash": workspace["working_copy"]["current_sha256"],
                "workspace_id": workspace["workspace_id"],
            },
        )
    ]
    assert mcp.bound_session is not None


def test_persisted_workspace_resume_rejects_non_active_workspace(
    tmp_path: Path,
) -> None:
    class NoResumeCallsMCP(FakeMCP):
        async def call_workspace_tool(self, name: str, arguments: dict) -> dict:
            raise AssertionError(f"unexpected workspace tool: {name} {arguments!r}")

    database_path = tmp_path / "host.sqlite3"
    store = HostStore(database_path)
    workspace = _persisted_workspace_state()
    store.set_host_state("active_workspace", workspace)
    config = HostConfig(
        database_path=database_path,
        static_dir=tmp_path / "missing-dist",
    )

    with TestClient(
        create_app(config, store=store, mcp=NoResumeCallsMCP()),
        base_url="http://localhost:8765",
    ) as client:
        authenticate_client(client, config)
        response = client.post(
            "/api/v1/workspaces/not-the-active-workspace/resume",
            json={},
            headers=_browser_csrf_headers(client),
        )

    assert response.status_code == 404


def test_persisted_workspace_resume_fails_closed_on_working_copy_drift(
    tmp_path: Path,
) -> None:
    class DriftedWorkspaceMCP(FakeMCP):
        def __init__(self, workspace: dict) -> None:
            super().__init__()
            self.workspace = workspace
            self.workspace_calls: list[str] = []
            self.read_calls: list[str] = []

        async def call_workspace_tool(self, name: str, arguments: dict) -> dict:
            self.workspace_calls.append(name)
            if name == "workspace.status":
                return _workspace_status_result(self.workspace)
            return await super().call_workspace_tool(name, arguments)

        async def call_read_tool(self, name: str, arguments: dict) -> dict:
            self.read_calls.append(name)
            return await super().call_read_tool(name, arguments)

    database_path = tmp_path / "host.sqlite3"
    store = HostStore(database_path)
    persisted = _persisted_workspace_state()
    store.set_host_state("active_workspace", persisted)
    mcp = DriftedWorkspaceMCP(_persisted_workspace_state(current_hash="b" * 64))
    config = HostConfig(
        database_path=database_path,
        static_dir=tmp_path / "missing-dist",
    )

    with TestClient(
        create_app(config, store=store, mcp=mcp),
        base_url="http://localhost:8765",
    ) as client:
        authenticate_client(client, config)
        response = client.post(
            f"/api/v1/workspaces/{persisted['workspace_id']}/resume",
            json={},
            headers=_browser_csrf_headers(client),
        )
        status_payload = client.get("/api/v1/status").json()

    assert response.status_code == 409
    assert "identity" in response.json()["detail"]
    assert mcp.workspace_calls == ["workspace.status"]
    assert mcp.read_calls == []
    assert mcp.bound_session is None
    assert status_payload["engineeringSystem"]["state"] == "degraded"
    assert store.get_host_state("active_workspace") == persisted


def test_persisted_workspace_resume_requires_unchanged_original_source(
    tmp_path: Path,
) -> None:
    class OriginalDriftMCP(FakeMCP):
        def __init__(self, workspace: dict) -> None:
            super().__init__()
            self.workspace = workspace
            self.workspace_calls: list[str] = []

        async def call_workspace_tool(self, name: str, arguments: dict) -> dict:
            self.workspace_calls.append(name)
            if name == "workspace.status":
                return _workspace_status_result(self.workspace)
            return await super().call_workspace_tool(name, arguments)

    database_path = tmp_path / "host.sqlite3"
    store = HostStore(database_path)
    persisted = _persisted_workspace_state()
    store.set_host_state("active_workspace", persisted)
    drifted = _persisted_workspace_state()
    drifted["source"]["original_state"] = "changed"
    drifted["source"]["original_current_sha256"] = "c" * 64
    mcp = OriginalDriftMCP(drifted)
    config = HostConfig(
        database_path=database_path,
        static_dir=tmp_path / "missing-dist",
    )

    with TestClient(
        create_app(config, store=store, mcp=mcp),
        base_url="http://localhost:8765",
    ) as client:
        authenticate_client(client, config)
        response = client.post(
            f"/api/v1/workspaces/{persisted['workspace_id']}/resume",
            json={},
            headers=_browser_csrf_headers(client),
        )

    assert response.status_code == 409
    assert "원본 프로젝트" in response.json()["detail"]
    assert mcp.workspace_calls == ["workspace.status"]
    assert mcp.bound_session is None
    assert store.get_host_state("active_workspace") == persisted


def test_persisted_workspace_resume_honors_global_target_lease(
    tmp_path: Path,
) -> None:
    class NoResumeCallsMCP(FakeMCP):
        async def call_workspace_tool(self, name: str, arguments: dict) -> dict:
            raise AssertionError(f"unexpected workspace tool: {name} {arguments!r}")

    database_path = tmp_path / "host.sqlite3"
    store = HostStore(database_path)
    workspace = _persisted_workspace_state()
    store.set_host_state("active_workspace", workspace)
    store.acquire_target_operation_lease(
        operation_id="another-target-operation",
        owner_kind="native_project_create",
        holder_token=OTHER_HOLDER_TOKEN_VALUE,
        target_identity={"target": "another-operation"},
    )
    config = HostConfig(
        database_path=database_path,
        static_dir=tmp_path / "missing-dist",
    )

    with TestClient(
        create_app(config, store=store, mcp=NoResumeCallsMCP()),
        base_url="http://localhost:8765",
    ) as client:
        authenticate_client(client, config)
        response = client.post(
            f"/api/v1/workspaces/{workspace['workspace_id']}/resume",
            json={},
            headers=_browser_csrf_headers(client),
        )

    assert response.status_code == 409
    assert "serialized" in response.json()["detail"]


def test_workspace_switch_clears_previous_agent_session_before_launch_failure(
    tmp_path: Path,
) -> None:
    class FailingSecondLaunchMCP(FakeMCP):
        def __init__(self) -> None:
            super().__init__()
            self.workspace_count = 0
            self.launch_count = 0

        async def call_workspace_tool(self, name: str, arguments: dict) -> dict:
            if name == "workspace.open_native":
                self.workspace_count += 1
                result = await super().call_workspace_tool(name, arguments)
                suffix = str(self.workspace_count)
                result["workspace_id"] = f"workspace-{suffix}"
                result["revision_id"] = f"revision-{suffix}"
                result["source"]["sha256"] = suffix * 64
                result["working_copy"]["path"] = (
                    f"C:\\workspaces\\working\\machine-{suffix}.xgwx"
                )
                result["working_copy"]["baseline_sha256"] = suffix * 64
                result["working_copy"]["current_sha256"] = suffix * 64
                return result
            if name == "xg5000.open_project":
                self.launch_count += 1
                if self.launch_count == 2:
                    raise MCPToolError("second launch rejected")
            return await super().call_workspace_tool(name, arguments)

    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
    )
    mcp = FailingSecondLaunchMCP()
    with TestClient(
        create_app(config, mcp=mcp),
        base_url="http://localhost:8765",
    ) as client:
        authenticate_client(client, config)
        first = client.post(
            "/api/v1/workspaces/open-local",
            json={
                "sourcePath": r"C:\projects\machine-1.xgwx",
                "openInXg5000": True,
            },
        )
        assert first.status_code == 200
        assert mcp.bound_session["project_path"].endswith("machine-1.xgwx")

        second = client.post(
            "/api/v1/workspaces/open-local",
            json={
                "sourcePath": r"C:\projects\machine-2.xgwx",
                "openInXg5000": True,
            },
        )
        assert second.status_code == 409
        status = client.get("/api/v1/status").json()

    assert status["workspace"]["workspaceId"] == "workspace-2"
    assert status["engineeringSystem"]["readReady"] is False
    assert status["engineeringSystem"]["sessionVerified"] is False
    assert mcp.bound_session is None


def test_restart_attach_reuses_persisted_working_copy_identity(
    tmp_path: Path,
) -> None:
    class RecordingMCP(FakeMCP):
        def __init__(self) -> None:
            super().__init__()
            self.attach_arguments: list[dict] = []

        async def call_read_tool(self, name: str, arguments: dict) -> dict:
            if name == "xg5000.attach":
                self.attach_arguments.append(dict(arguments))
            return await super().call_read_tool(name, arguments)

    database_path = tmp_path / "host.sqlite3"
    store = HostStore(database_path)
    working_path = r"C:\workspaces\restored\machine.xgwx"
    working_hash = "a" * 64
    store.set_host_state(
        "active_workspace",
        {
            "workspace_id": "11111111-1111-4111-8111-111111111111",
            "revision_id": "22222222-2222-4222-8222-222222222222",
            "source_kind": "local_file",
            "vendor": "ls",
            "engineering_system": "XG5000",
            "created_at": "2026-07-28T12:00:00+00:00",
            "source": {
                "original_path": r"C:\projects\machine.xgwx",
                "captured_path": r"C:\workspaces\source\machine.xgwx",
                "sha256": working_hash,
                "original_state": "unchanged",
                "original_current_sha256": working_hash,
            },
            "working_copy": {
                "path": working_path,
                "baseline_sha256": working_hash,
                "current_sha256": working_hash,
                "state": "clean",
            },
        },
    )
    mcp = RecordingMCP()
    config = HostConfig(
        database_path=database_path,
        static_dir=tmp_path / "missing-dist",
    )

    with TestClient(
        create_app(config, store=store, mcp=mcp),
        base_url="http://localhost:8765",
    ) as client:
        authenticate_client(client, config)
        restored = client.post(
            "/api/v1/xg5000/attach",
            json={"pid": 101, "hwnd": 202},
        )
        explicit = client.post(
            "/api/v1/xg5000/attach",
            json={
                "pid": 303,
                "hwnd": 404,
                "projectPath": r"C:\explicit\machine.xgwx",
                "projectHash": "b" * 64,
            },
        )

    assert restored.status_code == 200
    assert restored.json()["session"]["projectPath"] == working_path
    assert restored.json()["session"]["projectHash"] == working_hash
    assert explicit.status_code == 200
    assert mcp.attach_arguments == [
        {
            "pid": 101,
            "hwnd": 202,
            "access_profile": "analysis",
            "project_path": working_path,
            "project_hash": working_hash,
        },
        {
            "pid": 303,
            "hwnd": 404,
            "access_profile": "analysis",
            "project_path": r"C:\explicit\machine.xgwx",
            "project_hash": "b" * 64,
        },
    ]


def test_active_agent_run_can_be_cancelled_idempotently(tmp_path: Path) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    repository = ModelConnectionRepository(store, MemorySecretStore())
    repository.save(
        base_url="http://localhost:4000/v1",
        model="fake-model",
        timeout_seconds=30,
        api_key=None,
        custom_headers={},
        capability_verified_at=datetime.now(timezone.utc),
    )
    config = HostConfig(
        database_path=tmp_path / "host.sqlite3",
        static_dir=tmp_path / "missing-dist",
    )
    app = create_app(
        config,
        store=store,
        mcp=FakeMCP(),
        model_repository=repository,
        runtime_factory=lambda _settings, _mcp: BlockingRuntime(),
    )

    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        created = client.post(
            "/api/v1/conversations",
            json={"title": "Cancel test"},
        )
        conversation_id = created.json()["conversationId"]
        sent = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={
                "content": "Run until cancelled",
                "operationId": "operation-cancel-0001",
            },
            headers={"Idempotency-Key": "operation-cancel-0001"},
        )
        assert sent.status_code == 202
        run_id = sent.json()["runId"]

        cancelled = client.post(f"/api/v1/runs/{run_id}/cancel")
        duplicate = client.post(f"/api/v1/runs/{run_id}/cancel")

    assert cancelled.status_code == 200
    assert cancelled.json() == {
        "runId": run_id,
        "state": "cancelled",
        "terminal": True,
        "idempotent": False,
        "detail": "run cancelled",
    }
    assert duplicate.status_code == 200
    assert duplicate.json() == {
        "runId": run_id,
        "state": "cancelled",
        "terminal": True,
        "idempotent": True,
        "detail": "run was already terminal",
    }
