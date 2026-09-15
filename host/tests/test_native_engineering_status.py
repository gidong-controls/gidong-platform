from __future__ import annotations

import pytest

from controlstudio_host.native_engineering_status import (
    derive_native_engineering_status,
    validate_native_engineering_status,
)
from controlstudio_host.agent_protocol import assert_agent_safe_payload


_DIGEST = "a" * 64


def handoff() -> dict[str, str]:
    return {
        "schemaVersion": "1",
        "nativeProjectId": "native-project-1",
        "planningConversationId": "planning-conversation-1",
        "planningMessageId": "planning-message-1",
        "reviewedPlanDigest": _DIGEST,
        "projectBriefDigest": "b" * 64,
        "attachmentRevisionDigest": "c" * 64,
        "buildBundleId": "build-bundle-1",
        "buildReceiptSchemaVersion": "controlstudio-native-build-receipt/v3",
        "buildReceiptDigest": "d" * 64,
        "reopenReceiptSchemaVersion": "controlstudio-native-reopen-receipt/v2",
        "reopenReceiptDigest": "e" * 64,
        "projectSha256": "f" * 64,
    }


def reviewed_plan(*verification_methods: str) -> dict[str, object]:
    return {
        "schemaVersion": "2",
        "objective": "검토 완료된 오프라인 제어 프로젝트를 후속 검증한다.",
        "requirementSources": [
            {
                "field": "sequence",
                "quote": "준비 확인 후 한 칸 이동하고 완료 횟수를 센다.",
            },
            {
                "field": "operating_modes",
                "quote": "자동 반복과 수동 1회 이동을 지원한다.",
            },
            {
                "field": "performance_and_acceptance",
                "quote": "5초 안에 완료되지 않으면 알람으로 정지한다.",
            },
        ],
        "sections": [
            {
                "items": [
                    {"verificationMethod": method}
                    for method in verification_methods
                ]
            }
        ],
    }


def native_project_result() -> dict[str, object]:
    return {
        "reopenVerification": {
            "state": "verified",
            "receipt": {
                "status": "REOPEN_VERIFIED",
                "programReadback": {
                    "programName": "DaeKong_Index_Sim",
                    "language": "LD",
                    "rungCount": 2,
                    "exactInstructionReadback": [
                        "LOAD M0000",
                        "OUT P00000",
                        "END",
                    ],
                    "programCheckPassed": True,
                },
            },
        }
    }


def test_reviewed_plan_criteria_are_sufficient_when_brief_omits_acceptance() -> None:
    status = derive_native_engineering_status(
        handoff=handoff(),
        reviewed_plan=reviewed_plan("XG5000 Program Check 오류 0건"),
        project_brief={
            "machine_purpose": "대동인덱스 PC 모의 운전",
            "sequence": "  ",
            "safety_and_interlocks": "",
        },
        native_project_result=native_project_result(),
    )

    assert status["completionCriteria"] == ["XG5000 Program Check 오류 0건"]
    assert "projectSha256" not in status
    assert status["reviewedProgram"] == {
        "programName": "DaeKong_Index_Sim",
        "language": "LD",
        "instructionCount": 3,
        "rungCount": 2,
        "source": ["LOAD M0000", "OUT P00000", "END"],
        "programCheckPassed": True,
        "independentReopenVerified": True,
    }
    assert status["reviewedRequirements"]["machinePurpose"] == (
        "대동인덱스 PC 모의 운전"
    )
    assert status["reviewedRequirements"]["sequence"] == (
        "준비 확인 후 한 칸 이동하고 완료 횟수를 센다."
    )
    assert status["reviewedRequirements"]["safetyAndInterlocks"] is None
    assert status["reviewedRequirements"]["operatingModes"] == (
        "자동 반복과 수동 1회 이동을 지원한다."
    )
    assert status["reviewedRequirements"]["performanceAndAcceptance"] == (
        "5초 안에 완료되지 않으면 알람으로 정지한다."
    )
    assert_agent_safe_payload({"native_engineering_status": status})
    assert validate_native_engineering_status(status) == status


def test_status_still_requires_at_least_one_reviewed_completion_criterion() -> None:
    with pytest.raises(
        ValueError,
        match="native engineering completion criteria are empty",
    ):
        derive_native_engineering_status(
            handoff=handoff(),
            reviewed_plan=reviewed_plan(),
            project_brief={"machine_purpose": "대동인덱스 PC 모의 운전"},
            native_project_result=native_project_result(),
        )
