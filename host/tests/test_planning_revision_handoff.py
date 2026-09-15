from pathlib import Path

import pytest

from controlstudio_host.models import RunState
from controlstudio_host.native_projects import (
    NativeProjectConflict,
    NativeProjectRepository,
)
from controlstudio_host.store import HostStore


def _handoff_provenance(native_project_id: str, planning_id: str) -> dict[str, str]:
    return {
        "nativeProjectId": native_project_id,
        "planningConversationId": planning_id,
    }


def test_planning_conversation_can_continue_and_create_a_new_handoff(
    tmp_path: Path,
) -> None:
    store = HostStore(tmp_path / "controlstudio.sqlite3")
    planning = store.create_conversation(
        title="Indexer",
        workflow="new_project_planning",
        project_brief={"project_name": "Indexer"},
    )

    first, first_created = store.create_engineering_handoff_conversation(
        title="Indexer engineering v1",
        native_project_id="native-v1",
        planning_conversation_id=planning.id,
        workspace_id="workspace-v1",
        revision_id="revision-v1",
        engineering_session_id="session-v1",
        provenance=_handoff_provenance("native-v1", planning.id),
    )
    assert first_created is True

    message, run, created = store.create_user_message_and_run(
        conversation_id=planning.id,
        content="Fix the failed acceptance behavior in the retained draft.",
        operation_id="planning-revision-v2",
        workflow="new_project_planning",
        project_brief={"project_name": "Indexer"},
    )
    assert created is True
    assert message is not None
    assert run is not None

    # Finish the planning run before the immutable v2 handoff is created.
    store.update_run_state(run_id=run.id, state=RunState.COMPLETED)
    second, second_created = store.create_engineering_handoff_conversation(
        title="Indexer engineering v2",
        native_project_id="native-v2",
        planning_conversation_id=planning.id,
        workspace_id="workspace-v2",
        revision_id="revision-v2",
        engineering_session_id="session-v2",
        provenance=_handoff_provenance("native-v2", planning.id),
    )

    assert second_created is True
    assert second.id != first.id
    assert second.workflow == "engineering"


def test_new_reviewed_plan_revision_can_allocate_while_exact_replay_is_blocked(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "controlstudio.sqlite3"
    store = HostStore(database_path)
    planning = store.create_conversation(
        title="Indexer",
        workflow="new_project_planning",
        project_brief={"project_name": "Indexer"},
    )
    old_digest = "1" * 64
    new_digest = "2" * 64
    store.create_engineering_handoff_conversation(
        title="Indexer engineering v1",
        native_project_id="native-v1",
        planning_conversation_id=planning.id,
        workspace_id="workspace-v1",
        revision_id="revision-v1",
        engineering_session_id="session-v1",
        provenance={
            "nativeProjectId": "native-v1",
            "planningConversationId": planning.id,
            "planningMessageId": "plan-v1",
            "reviewedPlanDigest": old_digest,
        },
    )
    repository = NativeProjectRepository(database_path)

    new_request = {
        "reviewedPlan": {
            "conversationId": planning.id,
            "messageId": "plan-v2",
            "planDigest": new_digest,
        }
    }
    operation, created = repository.allocate(
        operation_id="11111111-1111-4111-8111-111111111111",
        request_digest="new-request",
        actor_id="operator",
        request=new_request,
        destination_directory=str(tmp_path / "v2"),
    )

    assert created is True
    assert operation.state == "ALLOCATED"

    with pytest.raises(
        NativeProjectConflict,
        match="reviewed plan revision already has an engineering handoff",
    ):
        repository.allocate(
            operation_id="22222222-2222-4222-8222-222222222222",
            request_digest="old-request",
            actor_id="operator",
            request={
                "reviewedPlan": {
                    "conversationId": planning.id,
                    "messageId": "plan-v1",
                    "planDigest": old_digest,
                }
            },
            destination_directory=str(tmp_path / "v1-replay"),
        )
