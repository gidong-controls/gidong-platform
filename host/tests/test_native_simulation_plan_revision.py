import asyncio
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from controlstudio_host.api_models import ProjectPlanArtifactV1
from controlstudio_host.native_projects import (
    NativeProjectConflict,
    NativeProjectOperation,
    NativeProjectService,
    NativeProjectSpec,
    _native_bundle_description,
)
from controlstudio_host.project_planning import (
    calculate_project_plan_attachment_revision_digest,
    expected_project_plan_semantic_items,
    project_plan_review_item_ids,
)

PROJECT_HASH = "9" * 64


class SigningGateway:
    def sign_mutation_authorization(self, claims):
        return {**claims, "signature": "signed"}


class ResumeRepository:
    def begin_completed_verification(self, operation_id):
        return True


class ResumeGateway(SigningGateway):
    async def call_read_tool(self, name, arguments):
        if name == "xg5000.session_status":
            # Simulate an MCP restart: the durable Host session exists, but
            # the in-memory reviewed-build binding is no longer available.
            return {}
        assert name == "xg5000.instances"
        return {"instances": [{"pid": 30, "hwnd": 40, "visible": True}]}

    async def call_workspace_tool(self, name, arguments):
        assert name == "project.verify_native_simulation"
        self.simulation_arguments = arguments
        return {"ok": False}


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _artifact(
    *, bindings: bool, follow_up_display: bool = False, message_source: bool = False
) -> dict[str, object]:
    confirmed = {
        "status": "confirmed",
        "reason": "The collection is explicitly defined.",
        "evidence": ["fixture authority"],
        "basis": None,
    }

    def omitted(reason: str, basis: str) -> dict[str, object]:
        return {
            "status": "not_applicable",
            "reason": reason,
            "evidence": ["fixture authority"],
            "basis": basis,
        }

    design: dict[str, object] = {
        "schemaVersion": "2",
        "collectionScope": {
            "ioPoints": confirmed,
            "operatingModes": omitted("One fixed mode.", "single_mode_operation"),
            "controlNetworks": confirmed,
            "timers": omitted("No timing behavior.", "no_timing_behavior"),
            "sequences": omitted("No sequence behavior.", "no_sequence_behavior"),
            "alarms": omitted("No alarm behavior.", "no_alarm_behavior"),
            "recoveryRules": omitted("No recovery behavior.", "no_recovery_behavior"),
            "safetyRules": omitted(
                "Only internal bits are used.", "no_physical_or_hazard_outputs"
            ),
        },
        "ioPoints": [
            {
                "signalId": "permit",
                "symbol": "PERMIT",
                "address": "M00000",
                "kind": "digital_input",
                "description": "simulation permit",
                "normalState": "off",
                "safeState": "not_applicable",
            },
            {
                "signalId": "run_output",
                "symbol": "RUN_OUTPUT",
                "address": "M00001",
                "kind": "internal_bit",
                "description": "simulation output",
                "normalState": "off",
                "safeState": "off",
            },
        ],
        "operatingModes": [],
        "controlNetworks": [
            {
                "networkId": "run_network",
                "title": "Run output",
                "modeId": None,
                "stepId": None,
                "permitAll": [{"signalId": "permit", "state": "on"}],
                "outputSignalId": "run_output",
            }
        ],
        "ladderRungs": [
            {
                "rungId": "run_output_rung",
                "description": "Drive the internal output from the permit.",
                "condition": {
                    "kind": "contact",
                    "sourceType": "signal",
                    "sourceId": "permit",
                    "state": "on",
                },
                "action": {
                    "instruction": "OUT",
                    "targetType": "signal",
                    "targetId": "run_output",
                },
            }
        ],
        "timers": [],
        "sequences": [],
        "alarms": [],
        "recoveryRules": [],
        "safetyRules": [],
    }
    if bindings:
        design["indexAcceptanceBindings"] = {
            "schemaVersion": "controlstudio-index-acceptance-bindings/v1",
            "signals": {"automatic_start": "permit", "move_command": "run_output"},
            "modes": {},
            "alarms": {},
            "timerId": "unused_timer",
        }
    artifact: dict[str, object] = {
        "schemaVersion": "2",
        "projectName": "test",
        "status": "ready_for_review",
        "summary": "follow-up display" if follow_up_display else "base display",
        "objective": "Verify a reviewed internal-bit program.",
        "requirementSources": (
            [{
                "field": "sequence",
                "quote": "permit controls the output",
                **({"messageId": "requirement-message-1"} if message_source else {}),
            }]
            if follow_up_display
            else []
        ),
        "attachments": [],
        "technicalConfiguration": {
            "plcSeries": "XGK",
            "cpuModel": "XGK-CPUE",
            "programName": "MAIN",
            "language": "LD",
            "description": "typed compiler fixture",
        },
        "design": design,
        "sections": [],
        "openDecisions": [],
        "completeness": {
            "totalItems": 0,
            "confirmed": 0,
            "proposed": 0,
            "unknown": 0,
            "conflicting": 0,
            "notApplicable": 0,
            "blocking": 0,
        },
    }
    canonical = ProjectPlanArtifactV1.model_validate(artifact).model_dump(
        mode="json", by_alias=True
    )
    rows = expected_project_plan_semantic_items(canonical)
    sections: list[dict[str, object]] = []
    for row in rows:
        if not sections or sections[-1]["sectionId"] != row["sectionId"]:
            sections.append(
                {"sectionId": row["sectionId"], "title": row["sectionId"], "items": []}
            )
        sections[-1]["items"].append(
            {
                "itemId": row["itemId"],
                "text": row["text"],
                "status": "proposed",
                "source": "agent",
                "blocking": False,
            }
        )
    canonical["sections"] = sections
    canonical["completeness"] = {
        "totalItems": len(rows),
        "confirmed": 0,
        "proposed": len(rows),
        "unknown": 0,
        "conflicting": 0,
        "notApplicable": 0,
        "blocking": 0,
    }
    return ProjectPlanArtifactV1.model_validate(canonical).model_dump(mode="json", by_alias=True)


def test_message_requirement_source_is_a_distinct_human_review_item():
    legacy = _artifact(bindings=False, follow_up_display=True)
    sourced = _artifact(
        bindings=False, follow_up_display=True, message_source=True
    )
    assert _digest(legacy) != _digest(sourced)
    item_id = "requirement/message/requirement-message-1/sequence"
    assert item_id in project_plan_review_item_ids(sourced)
    assert item_id not in project_plan_review_item_ids(legacy)


def _bundle(artifact: dict[str, object], *, digest_seed: str) -> dict[str, object]:
    return NativeProjectService.build_bundle(
        NativeProjectSpec(
            operation_id=f"{digest_seed * 8}-{digest_seed * 4}-4{digest_seed * 3}-8{digest_seed * 3}-{digest_seed * 12}",
            project_name="test",
            workspace_parent=Path.cwd(),
            plc_series="XGK",
            cpu_model="XGK-CPUE",
            program_name="MAIN",
            language="LD",
            description="typed compiler fixture",
            xg5000_pid=10,
            xg5000_hwnd=20,
            planning_conversation_id="conversation",
            planning_message_id=f"message-{digest_seed}",
            planning_plan_digest=_digest(artifact),
            reviewed_section_ids=tuple(
                section["sectionId"] for section in artifact["sections"]
            ),
            reviewed_item_ids=tuple(project_plan_review_item_ids(artifact)),
            project_brief_digest=digest_seed * 64,
            attachment_revision_digest=calculate_project_plan_attachment_revision_digest([]),
            planning_artifact=artifact,
        )
    )


def bundles():
    return (
        _bundle(_artifact(bindings=False), digest_seed="5"),
        _bundle(_artifact(bindings=True, follow_up_display=True), digest_seed="7"),
    )


def test_acceptance_bindings_are_exact_human_review_items():
    artifact = _artifact(bindings=True)
    section = next(
        item
        for item in artifact["sections"]
        if item["sectionId"] == "native-simulation-profile"
    )
    by_id = {item["itemId"]: json.loads(item["text"])["value"] for item in section["items"]}
    automatic = by_id["design/indexAcceptanceBindings/signals/automatic_start"]
    assert automatic == {
        "role": "automatic_start",
        "signalId": "permit",
        "signalName": "simulation permit",
        "signalSymbol": "PERMIT",
    }
    assert by_id["design/indexAcceptanceBindings/timer"]["timerId"] == "unused_timer"
    assert set(project_plan_review_item_ids(artifact)) >= set(by_id)


def operation(base, destination: Path):
    now = datetime.now(timezone.utc)
    project_path = destination / "test.xgwx"
    request = {
        "projectName": "test",
        "plcSeries": "XGK",
        "cpuModel": "XGK-CPUE",
        "programName": "MAIN",
        "language": "LD",
        "description": "typed compiler fixture",
        "workspaceParent": str(destination.parent),
        "xg5000Pid": 10,
        "xg5000Hwnd": 20,
        "buildBundle": base,
    }
    plan_digest = "b" * 64
    receipt = {
        "schemaVersion": "controlstudio-native-build-receipt/v3",
        "bundleId": base["bundleId"],
        "phase": "engineered_control_program",
        "status": "APPLIED_VERIFIED",
        "reviewedPlanDigest": base["reviewedPlan"]["planDigest"],
        "nativeReadback": {
            "projectName": "test",
            "projectPath": str(project_path),
            "projectSha256": PROJECT_HASH,
            "projectSize": 100,
            "destinationDirectory": str(destination),
            "plcSeries": "XGK",
            "cpuModel": "XGK-CPUE",
            "programName": "MAIN",
            "language": "LD",
            "description": _native_bundle_description(base),
            "identityVerified": True,
        },
        "programReadback": {
            "rungCount": len(base["compiledProgram"]["rungIds"]),
            "rungIds": base["compiledProgram"]["rungIds"],
            "ladderIrSha256": base["compiledProgram"]["ladderIrSha256"],
            "sourceSha256": base["compiledProgram"]["sourceSha256"],
            "exactInstructionReadback": base["compiledProgram"]["source"].splitlines(),
            "finalView": "LD",
            "programCheckPassed": True,
            "savedProjectSha256": PROJECT_HASH,
        },
    }
    result = {
        "operation": "project.create_native",
        "identityVerified": True,
        "xg5000Pid": 10,
        "xg5000Hwnd": 20,
        "planDigest": plan_digest,
        "buildBundleId": base["bundleId"],
        "reviewedPlanDigest": base["reviewedPlan"]["planDigest"],
        "controllerConnected": False,
        "plcWritePerformed": False,
        "projectPath": str(project_path),
        "projectSha256": PROJECT_HASH,
        "projectSize": 100,
        "destinationDirectory": str(destination),
        "buildBundleReceipt": receipt,
        "reopenVerification": {
            "state": "verified",
            "receipt": {"status": "REOPEN_VERIFIED"},
        },
        "workspaceRegistration": {
            "workspace": {"working_copy": {"current_sha256": PROJECT_HASH}}
        },
    }
    return NativeProjectOperation(
        id="operation",
        operation_id="11111111-1111-4111-8111-111111111111",
        state="COMPLETED",
        request_digest="a" * 64,
        actor_id="actor",
        request=request,
        plan_digest=plan_digest,
        destination_directory=str(destination),
        result=result,
        error_code=None,
        error_detail=None,
        created_at=now,
        updated_at=now,
        completed_at=now,
    )


def test_completed_reopen_preserves_unrelated_xg5000_windows(tmp_path):
    base, _ = bundles()
    value = operation(base, tmp_path)
    value.result["workspaceRegistration"] = {
        "state": "captured_not_bound",
        "workspace": {
            "workspace_id": "22222222-2222-4222-8222-222222222222",
            "working_copy": {
                "path": str(tmp_path / "protected" / "test.xgwx"),
                "current_sha256": PROJECT_HASH,
            },
        },
    }
    value.result["reopenVerification"] = {
        "state": "not_verified",
        "detail": "retry exact protected copy",
        "workspace": value.result["workspaceRegistration"]["workspace"],
    }
    gateway = AsyncMock()
    gateway.call_workspace_tool.return_value = {"cleaned_pids": []}
    gateway.call_read_tool.return_value = {
        "instances": [
            {"pid": 10, "hwnd": 20, "visible": True},
            {"pid": 30, "hwnd": 40, "visible": True},
            {"pid": 50, "hwnd": 60, "visible": True},
        ]
    }
    service = NativeProjectService(ResumeRepository(), gateway)
    service._complete_verified_native_tail = AsyncMock(return_value=(value, False))

    with (
        patch(
            "controlstudio_host.native_projects._native_project_plan_digest",
            return_value=value.plan_digest,
        ),
        patch(
            "controlstudio_host.native_projects._stored_creation_receipt_matches_reviewed_build",
            return_value=True,
        ),
    ):
        asyncio.run(
            service._continue_completed_verification(
                value,
                actor_id="operator",
                pid=10,
                hwnd=20,
                expected_request_digest=value.request_digest,
                reopened_pid=None,
                reopened_hwnd=None,
            )
        )

    service._complete_verified_native_tail.assert_awaited_once()
    call = service._complete_verified_native_tail.await_args
    assert call.kwargs["result"]["resumeProvenance"] == {
        "lifecycleMode": "completed_post_create_verification_continuation",
        "noNewDialog": True,
        "noIlInsert": True,
        "explicitSavedBuildReopen": True,
        "savedBuildRecoveryWithoutLiveSource": False,
        "unrelatedExistingInstancesPreserved": True,
    }


def test_binding_only_reviewed_artifact_preserves_actual_compiler_output(tmp_path):
    base, candidate = bundles()
    assert candidate["compiledProgram"] == base["compiledProgram"]
    revision = NativeProjectService(
        object(), SigningGateway()
    ).prepare_simulation_plan_revision(operation(base, tmp_path), candidate, actor_id="operator")
    assert revision["baseBuildBundleId"] == base["bundleId"]
    assert revision["candidateBundleId"] == candidate["bundleId"]
    assert revision["simulationReviewedPlan"] == candidate["reviewedPlan"]
    assert revision["authorization"]["allowed_operation"] == "project.verify_native_simulation"


@pytest.mark.parametrize("mutation", ["conversation", "logic", "technical", "attachment", "compiled"])
def test_revision_rejects_non_binding_authority_changes(tmp_path, mutation):
    base, candidate = bundles()
    if mutation == "conversation":
        candidate["reviewedPlan"]["conversationId"] = "other"
    elif mutation == "logic":
        candidate["engineeringPlan"]["design"]["ioPoints"].append(
            deepcopy(candidate["engineeringPlan"]["design"]["ioPoints"][0])
        )
    elif mutation == "technical":
        candidate["engineeringPlan"]["technicalConfiguration"]["plcSeries"] = "XGB"
    elif mutation == "attachment":
        candidate["engineeringPlan"]["attachments"] = [{"unexpected": True}]
    else:
        candidate["compiledProgram"]["sourceSha256"] = "f" * 64
    with pytest.raises(NativeProjectConflict):
        NativeProjectService(object(), SigningGateway()).prepare_simulation_plan_revision(
            operation(base, tmp_path), candidate, actor_id="operator"
        )


def test_revision_rejects_changed_current_project_or_tampered_receipt(tmp_path):
    base, candidate = bundles()
    value = operation(base, tmp_path)
    value.result["workspaceRegistration"]["workspace"]["working_copy"][
        "current_sha256"
    ] = "f" * 64
    with pytest.raises(NativeProjectConflict):
        NativeProjectService(object(), SigningGateway()).prepare_simulation_plan_revision(
            value, candidate, actor_id="operator"
        )
    value = operation(base, tmp_path)
    value.result["buildBundleReceipt"]["programReadback"]["sourceSha256"] = "f" * 64
    with pytest.raises(NativeProjectConflict):
        NativeProjectService(object(), SigningGateway()).prepare_simulation_plan_revision(
            value, candidate, actor_id="operator"
        )


def test_completed_resume_recreates_exact_reopen_binding_before_simulation(tmp_path):
    base, candidate = bundles()
    value = operation(base, tmp_path)
    revision = NativeProjectService(
        object(), SigningGateway()
    ).prepare_simulation_plan_revision(value, candidate, actor_id="operator")
    value.result["engineeredProgramReady"] = True
    value.result["workspaceBound"] = True
    value.result["workspaceRegistration"]["state"] = "captured_and_bound"
    value.result["workspaceRegistration"]["workspace"]["working_copy"]["path"] = str(
        tmp_path / "test.xgwx"
    )
    value.result["reopenVerification"]["session"] = {
        "session_id": "analysis-after-restart",
        "pid": 30,
        "hwnd": 40,
    }
    value.result["simulationVerification"] = {
        "state": "not_verified",
        "preflight": {"reasonCode": "EXPLICIT_INDEX_ACCEPTANCE_BINDINGS_REQUIRED"},
    }
    service = NativeProjectService(ResumeRepository(), ResumeGateway())
    rebound = {"session_id": "rebound-analysis", "pid": 30, "hwnd": 40}
    authorized = {
        "state": "verified",
        "session": {"session_id": "simulation-authorized", "pid": 30, "hwnd": 40},
        "receipt": {"status": "REOPEN_VERIFIED"},
    }
    service._rebind_completed_reopen_target = AsyncMock(return_value=rebound)
    service._verify_fresh_process_reopen = AsyncMock(return_value=authorized)
    service._complete_verified_native_tail = AsyncMock(return_value=(value, False))
    service._controlled_reopen_session_matches = lambda *args, **kwargs: True

    with (
        patch(
            "controlstudio_host.native_projects._native_project_plan_digest",
            return_value=value.plan_digest,
        ),
        patch(
            "controlstudio_host.native_projects._stored_creation_receipt_matches_reviewed_build",
            return_value=True,
        ),
    ):
        asyncio.run(
            service._continue_completed_verification(
                value,
                actor_id="operator",
                pid=10,
                hwnd=20,
                expected_request_digest=value.request_digest,
                reopened_pid=None,
                reopened_hwnd=None,
                run_simulation=True,
                simulation_plan_revision=revision,
            )
        )

    service._verify_fresh_process_reopen.assert_awaited_once_with(
        source_pid=10,
        registration=value.result["workspaceRegistration"],
        build_bundle=base,
        existing_session=rebound,
    )
    assert service._complete_verified_native_tail.await_args.kwargs["existing_session"] == (
        authorized["session"]
    )
    assert service._complete_verified_native_tail.await_args.kwargs[
        "simulation_plan_revision"
    ] == revision

    current_receipt = {
        "schemaVersion": "controlstudio-native-reopen-receipt/v2",
        "status": "REOPEN_VERIFIED",
        "buildBundleId": base["bundleId"],
        "reviewedPlanDigest": base["reviewedPlan"]["planDigest"],
        "projectReadback": {
            "projectSha256": PROJECT_HASH,
            "projectUnchanged": True,
        },
    }
    assert _digest(current_receipt) != revision["baseReopenReceiptDigest"]
    gateway = ResumeGateway()
    verify_service = NativeProjectService(ResumeRepository(), gateway)
    result = asyncio.run(
        verify_service._verify_native_simulation(
            reopen_verification={
                "state": "verified",
                "session": {
                    "session_id": "simulation-authorized",
                    "pid": 30,
                    "hwnd": 40,
                    "project_path": str(tmp_path / "test.xgwx"),
                    "project_hash": PROJECT_HASH,
                },
                "workspace": {},
                "receipt": current_receipt,
            },
            build_bundle=base,
            trial_id="trial",
            simulation_plan_revision=revision,
            base_build_receipt=value.result["buildBundleReceipt"],
        )
    )
    assert result["state"] in {"not_verified", "quarantined"}
    assert gateway.simulation_arguments["simulation_plan_revision"] == revision
