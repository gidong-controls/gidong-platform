from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from controlstudio_host.models import MessageRole, RunState
from controlstudio_host.store import (
    MIGRATIONS,
    ConversationActiveRunConflict,
    ConversationWorkflowConflict,
    HostStore,
    TargetOperationLeaseConflict,
)

FIRST_HOLDER_TOKEN_VALUE = "holder-a"
SECOND_HOLDER_TOKEN_VALUE = "holder-b"
THIRD_HOLDER_TOKEN_VALUE = "holder-c"
RECOVERY_HOLDER_TOKEN_VALUE = "holder-recovery"


def test_schema_is_migrated_and_reopening_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "host.sqlite3"
    HostStore(database_path)
    HostStore(database_path)

    with sqlite3.connect(database_path) as connection:
        migrations = connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert migrations == [
        (1, "initial_server_authority_store"),
        (2, "openai_compatible_model_settings"),
        (3, "idempotent_run_operations"),
        (4, "verified_model_capabilities"),
        (5, "server_owned_host_state"),
        (6, "durable_change_transactions"),
        (7, "host_target_operation_lease"),
        (8, "conversation_workflow_authority"),
        (9, "engineering_conversation_target_binding"),
        (10, "durable_change_receipt_acknowledgement"),
        (11, "digest_bound_project_plan_reviews"),
        (12, "project_plan_review_section_scope"),
    ]
    assert {
        "conversations",
        "messages",
        "runs",
        "events",
        "host_state",
        "change_transactions",
        "change_approvals",
        "mutation_leases",
        "target_operation_leases",
        "project_plan_reviews",
    } <= tables


def test_workflow_migration_preserves_legacy_conversations(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-host.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        for version, name, statements in MIGRATIONS:
            if version >= 8:
                break
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, name, applied_at)
                VALUES (?, ?, ?)
                """,
                (version, name, "2026-07-30T00:00:00+00:00"),
            )
        connection.execute(
            """
            INSERT INTO conversations(id, title, created_at, updated_at)
            VALUES ('legacy-planning', '기존 계획', ?, ?)
            """,
            ("2026-07-30T00:00:00+00:00", "2026-07-30T00:00:00+00:00"),
        )
        connection.execute(
            """
            INSERT INTO messages(
                id, conversation_id, sequence, role, content,
                metadata_json, created_at
            )
            VALUES (
                'legacy-message', 'legacy-planning', 1, 'user', '기존 브리프',
                '{"workflow":"new_project_planning"}',
                '2026-07-30T00:00:00+00:00'
            )
            """
        )

    migrated = HostStore(database_path).get_conversation("legacy-planning")

    assert migrated is not None
    assert migrated.workflow == "new_project_planning"
    assert migrated.project_brief is None
    with sqlite3.connect(database_path) as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]
    assert version == 12


def test_store_persists_and_clears_server_owned_host_state(tmp_path: Path) -> None:
    database_path = tmp_path / "host.sqlite3"
    store = HostStore(database_path)
    workspace = {
        "workspace_id": "11111111-1111-4111-8111-111111111111",
        "working_copy": {"path": "C:\\workspaces\\machine.xgwx"},
    }

    assert store.get_host_state("active_workspace") is None
    store.set_host_state("active_workspace", workspace)
    assert HostStore(database_path).get_host_state("active_workspace") == workspace
    store.set_host_state("active_workspace", None)
    assert store.get_host_state("active_workspace") is None


def test_target_operation_lease_serializes_and_restart_reclaims_only_exact_request(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "host.sqlite3"
    operation_id = str(uuid.uuid4())
    target = {"route": "/api/v1/xg5000/attach", "pid": 101, "hwnd": 202}
    first = HostStore(database_path)
    first.acquire_target_operation_lease(
        operation_id=operation_id,
        owner_kind="engineering_attach",
        holder_token=FIRST_HOLDER_TOKEN_VALUE,
        target_identity=target,
    )

    contender = HostStore(database_path)
    with pytest.raises(TargetOperationLeaseConflict):
        contender.acquire_target_operation_lease(
            operation_id=str(uuid.uuid4()),
            owner_kind="workspace_open",
            holder_token=SECOND_HOLDER_TOKEN_VALUE,
            target_identity={"route": "/api/v1/workspaces/open-local"},
        )

    restarted = HostStore(database_path)
    assert restarted.mark_previous_instance_target_leases_stale() == 1
    with pytest.raises(TargetOperationLeaseConflict):
        restarted.acquire_target_operation_lease(
            operation_id=str(uuid.uuid4()),
            owner_kind="engineering_attach",
            holder_token=THIRD_HOLDER_TOKEN_VALUE,
            target_identity=target,
        )
    recovered = restarted.acquire_target_operation_lease(
        operation_id=operation_id,
        owner_kind="engineering_attach",
        holder_token=RECOVERY_HOLDER_TOKEN_VALUE,
        target_identity=target,
    )
    assert recovered["state"] == "ACTIVE"
    assert recovered["operation_id"] == operation_id
    assert restarted.release_target_operation_lease(
        operation_id=operation_id,
        holder_token=RECOVERY_HOLDER_TOKEN_VALUE,
        reason="exact_request_revalidated",
    )


def test_store_owns_message_run_and_event_ordering(tmp_path: Path) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    conversation = store.create_conversation(title="Line 1")
    first = store.append_message(
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="Analyze the stop",
    )
    second = store.append_message(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="Collecting evidence",
        metadata={"authoritative": True},
    )
    assert first is not None
    assert second is not None
    assert (first.sequence, second.sequence) == (1, 2)

    run = store.create_run(
        conversation_id=conversation.id,
        request_message_id=first.id,
        state=RunState.ANALYZING,
    )
    assert run is not None
    completed = store.update_run_state(run_id=run.id, state=RunState.COMPLETED)
    assert completed is not None
    assert completed.state is RunState.COMPLETED

    detail = store.get_conversation(conversation.id)
    assert detail is not None
    assert [message.role for message in detail.messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert detail.messages[1].metadata == {"authoritative": True}
    assert detail.runs[0].state is RunState.COMPLETED

    events = store.list_events()
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert [event.event_type for event in events] == [
        "conversation.created",
        "message.appended",
        "message.appended",
        "run.created",
        "run.state_changed",
    ]


def test_conversation_event_stream_can_include_global_host_events(
    tmp_path: Path,
) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    selected = store.create_conversation(title="Selected")
    other = store.create_conversation(title="Other")
    store.append_event(
        event_type="host.status",
        payload={"status": "ready"},
    )
    store.append_event(
        event_type="tool.completed",
        payload={"tool_name": "project.list_programs"},
        conversation_id=selected.id,
    )
    store.append_event(
        event_type="tool.completed",
        payload={"tool_name": "program.read_ld"},
        conversation_id=other.id,
    )

    selected_only = store.list_events(conversation_id=selected.id)
    selected_with_global = store.list_events(
        conversation_id=selected.id,
        include_global=True,
    )

    assert [event.event_type for event in selected_only] == [
        "conversation.created",
        "tool.completed",
    ]
    assert [event.event_type for event in selected_with_global] == [
        "conversation.created",
        "host.status",
        "tool.completed",
    ]
    assert all(
        event.conversation_id in {None, selected.id}
        for event in selected_with_global
    )


def test_engineering_conversation_persists_exact_target_binding(
    tmp_path: Path,
) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    conversation = store.create_conversation(
        title="설비 분석",
        workspace_id="workspace-1",
        revision_id="revision-1",
        engineering_session_id="session-1",
    )

    restored = HostStore(store.database_path).get_conversation(conversation.id)

    assert restored is not None
    assert restored.workspace_id == "workspace-1"
    assert restored.revision_id == "revision-1"
    assert restored.engineering_session_id == "session-1"
    with pytest.raises(ValueError, match="must be complete"):
        store.create_conversation(
            title="불완전한 대상",
            workspace_id="workspace-1",
        )
    with pytest.raises(ValueError, match="only engineering"):
        store.create_conversation(
            title="계획",
            workflow="new_project_planning",
            project_brief={"project_name": "Line"},
            workspace_id="workspace-1",
            revision_id="revision-1",
            engineering_session_id="session-1",
        )


def test_user_message_and_run_are_one_idempotent_transaction(tmp_path: Path) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    conversation = store.create_conversation(title="Line 1")

    message, run, created = store.create_user_message_and_run(
        conversation_id=conversation.id,
        content="Analyze the stop",
        operation_id="operation-atomic-1",
    )
    duplicate_message, duplicate_run, duplicate_created = store.create_user_message_and_run(
        conversation_id=conversation.id,
        content="This must not be appended",
        operation_id="operation-atomic-1",
    )

    assert created is True
    assert message is not None and run is not None
    assert run.request_message_id == message.id
    assert duplicate_created is False
    assert duplicate_message is None
    assert duplicate_run is not None and duplicate_run.id == run.id
    detail = store.get_conversation(conversation.id)
    assert detail is not None
    assert [item.content for item in detail.messages] == ["Analyze the stop"]


def test_conversation_workflow_is_fixed_and_only_one_global_run_can_be_active(
    tmp_path: Path,
) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    brief = {
        "project_name": "포장기",
        "machine_purpose": "제품을 포장한다",
        "sequence": "",
        "hardware_and_io": "",
        "safety_and_interlocks": "",
    }
    conversation = store.create_conversation(
        title="신규 설계",
        workflow="new_project_planning",
        project_brief=brief,
    )
    assert conversation.workflow == "new_project_planning"
    assert conversation.project_brief == brief

    message, run, created = store.create_user_message_and_run(
        conversation_id=conversation.id,
        content="계획을 작성해 줘",
        operation_id="planning-run-1",
        workflow="new_project_planning",
    )
    assert created and message is not None and run is not None

    with pytest.raises(ConversationActiveRunConflict):
        store.create_user_message_and_run(
            conversation_id=conversation.id,
            content="동시에 실행하지 마",
            operation_id="planning-run-2",
            workflow="new_project_planning",
        )
    other_conversation = store.create_conversation(title="기존 프로젝트 분석")
    with pytest.raises(ConversationActiveRunConflict):
        store.create_user_message_and_run(
            conversation_id=other_conversation.id,
            content="다른 대화에서도 동시에 실행하지 마",
            operation_id="engineering-run-1",
            workflow="engineering",
        )
    with pytest.raises(ConversationWorkflowConflict):
        store.create_user_message_and_run(
            conversation_id=conversation.id,
            content="워크플로를 바꾸지 마",
            operation_id="planning-run-3",
            workflow="engineering",
        )

    store.update_run_state(run_id=run.id, state=RunState.COMPLETED)
    updated_brief = {**brief, "machine_purpose": "제품을 검사하고 포장한다"}
    _next_message, next_run, next_created = store.create_user_message_and_run(
        conversation_id=conversation.id,
        content="계획을 보완해 줘",
        operation_id="planning-run-4",
        workflow="new_project_planning",
        project_brief=updated_brief,
    )
    assert next_created and next_run is not None
    reloaded = HostStore(store.database_path).get_conversation(conversation.id)
    assert reloaded is not None
    assert reloaded.project_brief == updated_brief


def test_restart_reconciliation_fails_reads_and_quarantines_uncertain_changes(
    tmp_path: Path,
) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    conversation = store.create_conversation(title="Line 1")
    analysis = store.create_run(
        conversation_id=conversation.id,
        state=RunState.ANALYZING,
    )
    executing = store.create_run(
        conversation_id=conversation.id,
        state=RunState.EXECUTING,
    )
    assert analysis is not None and executing is not None

    assert store.reconcile_interrupted_runs() == 2
    assert store.get_run(analysis.id).state is RunState.FAILED
    assert store.get_run(executing.id).state is RunState.QUARANTINED
    assert store.reconcile_interrupted_runs() == 0
