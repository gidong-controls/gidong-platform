import asyncio
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from controlstudio_host.native_projects import (
    NativeProjectOperation,
    NativeProjectRepository,
    NativeProjectService,
    NativeProjectSpec,
    _native_simulation_failure_projection,
    _native_simulator_stop_state_verified,
    planning_native_failure_observation,
)
from controlstudio_host.store import HostStore

DIGEST = "a" * 64
PRIOR_DIGEST = "b" * 64


def operation(*, state="COMPLETED", result=None, error_code=None):
    now = datetime.now(timezone.utc)
    return NativeProjectOperation(
        id="operation",
        operation_id="operation",
        state=state,
        request_digest="c" * 64,
        actor_id="actor",
        request={
            "reviewedPlan": {
                "conversationId": "planning-conversation",
                "messageId": "reviewed-message",
                "planDigest": PRIOR_DIGEST,
                "projectBriefDigest": DIGEST,
            }
        },
        plan_digest="d" * 64,
        destination_directory="unused",
        result=result or {},
        error_code=error_code,
        error_detail="raw detail must never enter the planning observation",
        created_at=now,
        updated_at=now,
        completed_at=now,
    )


def project(value):
    return planning_native_failure_observation(
        value,
        conversation_id="planning-conversation",
        current_project_brief_digest=DIGEST,
        current_plan_digest="e" * 64,
    )


def test_owner_drawn_stop_band_is_accepted_without_fabricated_uia_controls():
    state = {
        "verified": True,
        "method": "xg5000-simulator-status-pane/v1",
        "classification": "STOP",
        "pid": 17364,
        "hwnd": 1379810,
        "foreground_pid": 17364,
        "foreground_hwnd": 1379810,
        "dpi": 96,
        "main_rect": [0, 0, 1142, 741],
        "status_candidate_count": 1,
        "status_text": None,
        "status_rect": [3, 717, 1139, 741],
        "status_relative_rect": [3, 717, 1139, 741],
        "status_bottom_gap": 0,
        "geometry_verified": True,
        "capture_method": "PIL.ImageGrab/foreground-status-pane",
        "sample_box": [8, 717, 98, 721],
        "sample_size": [90, 4],
        "owner_drawn_status_root": True,
        "consecutive_count": 3,
        "selected_run": None,
        "selected_stop": None,
        "samples": [
            {
                "index": index,
                "classification": "STOP",
                "pixel_count": 360,
                "dominant_rgb": [229, 20, 0],
                "dominant_share": 1.0,
                "stop_share": 1.0,
                "run_share": 0.0,
                "offline_share": 0.0,
                "stop_prototype_distance": 0.0,
            }
            for index in range(1, 4)
        ],
    }

    assert _native_simulator_stop_state_verified(state) is True
    assert _native_simulator_stop_state_verified({
        **state, "sample_box": [8, 718, 98, 722],
    }) is False


def test_structured_simulation_failure_is_lineage_bound_and_bounded():
    value = operation(result={
        "simulationVerification": {
            "state": "quarantined",
            "failure": {
                "classification": "UNRESOLVED_RUNTIME_FAILURE",
                "phase": "scenario_observation",
                "runtimeError": {"code": "SCENARIO_READ_TIMEOUT"},
                "projectUnchanged": True,
                "recovery": {"restorationVerified": False},
                "scenarioObservation": {
                    "observationLabel": "manual_second_move_after_new_press",
                    "inputAssignment": {"manual_start": 1},
                    "inputAssignmentScope": "last_requested_write",
                    "expected": {"motion": 1},
                    "observed": {"motion": 0},
                    "observationKind": "polled_timeout",
                },
            },
        }
    })

    observation = project(value)

    assert observation == {
        "schemaVersion": "controlstudio-planning-native-failure/v1",
        "operationId": "operation",
        "planningConversationId": "planning-conversation",
        "planningMessageId": "reviewed-message",
        "reviewedPlanDigest": PRIOR_DIGEST,
        "projectBriefDigest": DIGEST,
        "briefRelation": "current_project_brief",
        "revisionRelation": "previous_reviewed_plan",
        "operationState": "COMPLETED",
        "failureKind": "native_simulation",
        "observationStrength": "structured_failure_receipt",
        "errorCode": None,
        "classification": "UNRESOLVED_RUNTIME_FAILURE",
        "phase": "scenario_observation",
        "runtimeErrorCode": "SCENARIO_READ_TIMEOUT",
        "projectUnchanged": True,
        "safeRestorationVerified": False,
        "scenarioObservation": {
            "observationLabel": "manual_second_move_after_new_press",
            "inputAssignment": {"manual_start": 1},
            "inputAssignmentScope": "last_requested_write",
            "expected": {"motion": 1},
            "observed": {"motion": 0},
            "observationKind": "polled_timeout",
        },
    }
    assert "detail" not in observation


def test_host_accepts_bound_failed_scenario_values_without_promoting_success():
    project_hash = "9" * 64
    scenario = {
        "observationLabel": "manual_second_move_after_new_press",
        "inputAssignment": {"manual_start": 1},
        "inputAssignmentScope": "last_requested_write",
        "expected": {"motion": 1},
        "observed": {"motion": 0},
        "observationKind": "polled_timeout",
    }
    raw = {
        "ok": False,
        "verified": False,
        "operation": "project.verify_native_simulation",
        "project_unchanged": True,
        "controllerConnected": False,
        "plcWritePerformed": False,
        "simulation_failure": {
            "schemaVersion": "controlstudio-native-simulation-failure/v1",
            "classification": "UNRESOLVED_RUNTIME_FAILURE",
            "phase": "scenario_observation",
            "detail": "bounded failure",
            "runtimeError": {
                "code": "ScenarioObservationMismatch",
                "workerOperation": None,
                "timeoutSeconds": None,
                "dispatchState": None,
            },
            "dispatch": {
                "simulatorStart": "attempted",
                "programWrite": "attempted",
                "cpuRun": "attempted",
            },
            "baseline": {"capture": "attempted", "restore": "attempted"},
            "recovery": {
                "cpuStop": "attempted",
                "restorationVerified": False,
                "failureStages": ["baseline_restore"],
            },
            "projectHashBefore": project_hash,
            "projectHashAfter": project_hash,
            "projectUnchanged": True,
            "scenarioObservation": scenario,
        },
    }

    projected = _native_simulation_failure_projection(raw, project_hash=project_hash)

    assert projected is not None
    assert projected["scenarioObservation"] == scenario
    assert raw["verified"] is False
    tampered = {**raw, "simulation_failure": {
        **raw["simulation_failure"], "projectHashBefore": "8" * 64,
    }}
    assert _native_simulation_failure_projection(tampered, project_hash=project_hash) is None


def test_latest_success_suppresses_a_stale_embedded_failure():
    successful = operation(result={
        "engineeredProgramReady": True,
        "workspaceBound": True,
        "simulationVerification": {
            "state": "verified",
            "failure": {
                "classification": "PRE_DISPATCH_READ_FAILURE",
                "phase": "simulator_start",
                "runtimeError": {"code": "OLD_FAILURE"},
            },
        },
    })

    assert project(successful) is None


def test_profile_preflight_is_actionable_even_when_build_and_reopen_remain_ready():
    observation = {
        "schemaVersion": "controlstudio-native-simulation-profile-observation/v1",
        "reasonCode": "EXPLICIT_INDEX_ACCEPTANCE_BINDINGS_REQUIRED",
        "requiredDesignSection": "index_acceptance_bindings",
        "profilePresent": False,
        "signalIds": ["start", "motion"],
        "modeIds": ["automatic", "manual", "stopped"],
        "alarmIds": ["emergency", "door", "timeout"],
        "timerIds": ["move_timeout"],
        "bindings": None,
    }
    successful_build = operation(result={
        "engineeredProgramReady": True,
        "workspaceBound": True,
        "reopenVerification": {"state": "verified"},
        "simulationVerification": {
            "state": "not_verified",
            "preflight": observation,
        },
    })

    projected = project(successful_build)

    assert projected is not None
    assert projected["failureKind"] == "native_simulation_profile"
    assert projected["observationStrength"] == "structured_preflight_observation"
    assert projected["acceptanceProfile"] == observation

    historical = planning_native_failure_observation(
        successful_build,
        conversation_id="planning-conversation",
        current_project_brief_digest="f" * 64,
        current_plan_digest="e" * 64,
    )
    assert historical is not None
    assert historical["briefRelation"] == "previous_project_brief"
    assert historical["revisionRelation"] == "previous_reviewed_plan"
    assert historical["acceptanceProfile"] == observation


def test_profile_preflight_does_not_erase_completed_build_or_reopen_state():
    class Repository:
        def checkpoint_execution(self, operation_id, result):
            self.result = result

        def complete(self, operation_id, result):
            self.result = result
            return result

    repository = Repository()
    service = NativeProjectService(repository, object())
    spec = NativeProjectSpec(
        operation_id="11111111-1111-4111-8111-111111111111",
        project_name="test",
        workspace_parent=Path("unused"),
        plc_series="XGK",
        cpu_model="XGK-CPUE",
        program_name="MAIN",
        language="LD",
        description="test",
        xg5000_pid=1,
        xg5000_hwnd=2,
    )
    registration = {"state": "captured_and_bound", "workspace": {}}
    profile = {
        "schemaVersion": "controlstudio-native-simulation-profile-observation/v1",
        "reasonCode": "EXPLICIT_INDEX_ACCEPTANCE_BINDINGS_REQUIRED",
        "requiredDesignSection": "index_acceptance_bindings",
        "profilePresent": False,
        "signalIds": ["start"],
        "modeIds": ["automatic", "manual", "stopped"],
        "alarmIds": [],
        "timerIds": [],
        "bindings": None,
    }

    completed, _ = asyncio.run(service._complete_verified_native_tail(
        spec,
        build_bundle={},
        result={},
        created=False,
        resume=True,
        registration=registration,
        reopen_verification={"state": "verified"},
        simulation_verification={
            "state": "not_verified",
            "preflight": profile,
        },
        run_simulation=True,
    ))

    assert completed["engineeredProgramReady"] is True
    assert completed["workspaceBound"] is True
    assert registration["state"] == "captured_and_bound"
    assert "role-binding profile" in completed["workspaceBindingReason"]


def test_host_persists_only_profile_observation_bound_to_reviewed_bundle():
    profile = {
        "schemaVersion": "controlstudio-native-simulation-profile-observation/v1",
        "reasonCode": "EXPLICIT_INDEX_ACCEPTANCE_BINDINGS_REQUIRED",
        "requiredDesignSection": "index_acceptance_bindings",
        "profilePresent": False,
        "signalIds": ["start", "motion"],
        "modeIds": ["automatic", "manual", "stopped"],
        "alarmIds": ["timeout"],
        "timerIds": ["move_timeout"],
        "bindings": None,
    }
    bundle = {
        "bundleId": "1" * 64,
        "reviewedPlan": {"planDigest": "2" * 64},
        "compiledProgram": {
            "sourceSha256": "3" * 64,
            "ladderIrSha256": "4" * 64,
        },
        "engineeringPlan": {
            "design": {
                "ioPoints": [{"signalId": "start"}, {"signalId": "motion"}],
                "operatingModes": [
                    {"modeId": "automatic"},
                    {"modeId": "manual"},
                    {"modeId": "stopped"},
                ],
                "alarms": [{"alarmId": "timeout"}],
                "timers": [{"timerId": "move_timeout"}],
            }
        },
        "technicalConfiguration": {},
    }
    raw = {
        "ok": True,
        "verified": True,
        "operation": "project.verify_native_simulation",
        "project_unchanged": True,
        "controllerConnected": False,
        "plcWritePerformed": False,
        "preflight": {
            "state": "rejected",
            "simulatorActionPerformed": False,
            "detail": "untrusted runtime wording",
            "buildBundleId": "1" * 64,
            "reviewedPlanDigest": "2" * 64,
            "sourceSha256": "3" * 64,
            "ladderIrSha256": "4" * 64,
            "acceptanceProfile": profile,
        },
    }

    class Gateway:
        async def call_workspace_tool(self, name, arguments):
            return raw

    service = NativeProjectService(object(), Gateway())
    reopen = {
        "state": "verified",
        "session": {
            "session_id": "session",
            "pid": 10,
            "hwnd": 20,
            "project_path": "C:/workspace/test.xgwx",
            "project_hash": "5" * 64,
        },
        "workspace": {},
    }

    result = asyncio.run(service._verify_native_simulation(
        reopen_verification=reopen,
        build_bundle=bundle,
        trial_id="trial",
    ))

    assert result["state"] == "not_verified"
    assert result["preflight"] == profile
    assert "untrusted runtime wording" not in result["detail"]

    raw["preflight"]["sourceSha256"] = "6" * 64
    rejected = asyncio.run(service._verify_native_simulation(
        reopen_verification=reopen,
        build_bundle=bundle,
        trial_id="trial",
    ))
    assert rejected["state"] == "quarantined"
    assert "preflight" not in rejected
    assert "provenance is invalid" in rejected["detail"]


def test_other_conversation_is_rejected_but_changed_brief_is_historical():
    failed = operation(state="FAILED", error_code="NATIVE_PROJECT_PREPARATION_FAILED")
    assert planning_native_failure_observation(
        failed,
        conversation_id="another-conversation",
        current_project_brief_digest=DIGEST,
        current_plan_digest=None,
    ) is None
    historical = planning_native_failure_observation(
        failed,
        conversation_id="planning-conversation",
        current_project_brief_digest="f" * 64,
        current_plan_digest=None,
    )
    assert historical is not None
    assert historical["briefRelation"] == "previous_project_brief"
    assert historical["revisionRelation"] == "previous_reviewed_plan"


def test_mismatched_build_bundle_review_digest_is_not_projected():
    failed = operation(state="FAILED", error_code="NATIVE_PROJECT_PREPARATION_FAILED")
    failed.request["buildBundle"] = {
        "reviewedPlan": {**failed.request["reviewedPlan"], "planDigest": "f" * 64}
    }
    assert planning_native_failure_observation(
        failed,
        conversation_id="planning-conversation",
        current_project_brief_digest="e" * 64,
        current_plan_digest=None,
    ) is None


def test_repository_selects_newest_lineage_so_success_suppresses_old_failure():
    temporary = tempfile.TemporaryDirectory()
    try:
        directory = Path(temporary.name)
        database_path = directory / "host.sqlite3"
        store = HostStore(database_path)
        conversation = store.create_conversation(
            workflow="new_project_planning",
            project_brief={"project_name": "test"},
        )
        repository = NativeProjectRepository(database_path)

        def allocate(operation_id, message_id, plan_digest):
            return repository.allocate(
                operation_id=operation_id,
                request_digest=operation_id,
                actor_id="operator",
                request={
                    "reviewedPlan": {
                        "conversationId": conversation.id,
                        "messageId": message_id,
                        "planDigest": plan_digest,
                    }
                },
                destination_directory=str(directory / operation_id),
            )[0]

        failed = allocate(
            "11111111-1111-4111-8111-111111111111", "old-message", "1" * 64
        )
        repository.fail_allocated(
            failed.operation_id,
            error_code="NATIVE_PROJECT_PREPARATION_FAILED",
            error_detail="old failure",
        )
        latest = allocate(
            "22222222-2222-4222-8222-222222222222", "new-message", "2" * 64
        )
        repository.set_plan_digest(latest.operation_id, "3" * 64)
        assert repository.begin_execution(latest.operation_id)
        repository.complete(
            latest.operation_id,
            {"engineeredProgramReady": True, "workspaceBound": True},
        )

        selected = repository.latest_for_planning_conversation(conversation.id)
        assert selected is not None
        assert selected.operation_id == latest.operation_id
    finally:
        temporary.cleanup()
