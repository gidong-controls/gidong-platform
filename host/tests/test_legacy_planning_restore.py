from __future__ import annotations

import sqlite3
from pathlib import Path

from conftest import FakeMCP, authenticate_client
from fastapi.testclient import TestClient

from controlstudio_host.app import create_app
from controlstudio_host.config import HostConfig
from controlstudio_host.models import MessageRole
from controlstudio_host.store import HostStore


def _clear_project_brief(store: HostStore, conversation_id: str) -> None:
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE conversations SET project_brief_json = '{}' WHERE id = ?",
            (conversation_id,),
        )


def test_conversation_api_recovers_latest_legacy_planning_brief_without_persisting_it(
    tmp_path: Path,
) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    conversation = store.create_conversation(
        title="기존 검사기 계획",
        workflow="new_project_planning",
        project_brief={
            "project_name": "임시 이름",
            "machine_purpose": "마이그레이션 전 임시 목적",
            "sequence": "",
            "hardware_and_io": "",
            "safety_and_interlocks": "",
        },
    )
    store.append_message(
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=(
            "다음 요구사항으로 새 PLC 프로젝트 계획을 시작해 주세요.\n\n"
            "프로젝트 이름: 이전 검사기\n"
            "설비 목적: 이전 검사 목적\n"
            "알고 있는 동작 순서·공정: 이전 순서\n"
            "알고 있는 하드웨어·I/O·통신: 이전 I/O\n"
            "안전·인터록·운영 제약: 이전 제약"
        ),
        metadata={"workflow": "new_project_planning"},
    )
    store.append_message(
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=(
            "새 프로젝트 요구사항을 다음 내용으로 수정했습니다. "
            "기존 계획을 다시 검토해 주세요.\n\n"
            "프로젝트 이름: 최종 검사기\n"
            "설비 목적: 카메라로 검사하고\n"
            "불량품을 별도 슈트로 배출한다\n"
            "알고 있는 동작 순서·공정: 아직 모름\n"
            "알고 있는 하드웨어·I/O·통신: 카메라 Ethernet/IP\n"
            "안전·인터록·운영 제약: 아직 모름\n\n"
            "이 단계에서는 파일이나 XG5000을 변경하지 말고, 확인된 사실·가정·미결 질문을 "
            "구분한 검토 가능한 프로젝트 계획서를 작성해 주세요."
        ),
        metadata={"workflow": "new_project_planning"},
    )
    store.append_message(
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="카메라 모델은 아직 선정하지 않았습니다.",
        metadata={"workflow": "new_project_planning"},
    )
    _clear_project_brief(store, conversation.id)

    config = HostConfig(
        database_path=store.database_path,
        static_dir=tmp_path / "missing-dist",
    )
    app = create_app(config, store=store, mcp=FakeMCP())

    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        response = client.get(f"/api/v1/conversations/{conversation.id}")

    assert response.status_code == 200
    assert response.json()["projectBrief"] == {
        "projectName": "최종 검사기",
        "machinePurpose": "카메라로 검사하고\n불량품을 별도 슈트로 배출한다",
        "sequence": "",
        "hardwareAndIo": "카메라 Ethernet/IP",
        "safetyAndInterlocks": "",
        "operatingModes": "",
        "performanceAndAcceptance": "",
    }
    stored = store.get_conversation(conversation.id)
    assert stored is not None
    assert stored.project_brief is None


def test_conversation_api_does_not_recover_incomplete_latest_legacy_brief(
    tmp_path: Path,
) -> None:
    store = HostStore(tmp_path / "host.sqlite3")
    conversation = store.create_conversation(
        workflow="new_project_planning",
        project_brief={
            "project_name": "임시 이름",
            "machine_purpose": "마이그레이션 전 임시 목적",
        },
    )
    store.append_message(
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="프로젝트 이름: 이전 이름\n설비 목적: 이전 목적",
        metadata={"workflow": "new_project_planning"},
    )
    store.append_message(
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="프로젝트 이름: 목적이 없는 최신 브리프",
        metadata={"workflow": "new_project_planning"},
    )
    _clear_project_brief(store, conversation.id)

    config = HostConfig(
        database_path=store.database_path,
        static_dir=tmp_path / "missing-dist",
    )
    app = create_app(config, store=store, mcp=FakeMCP())

    with TestClient(app, base_url="http://localhost:8765") as client:
        authenticate_client(client, config)
        response = client.get(f"/api/v1/conversations/{conversation.id}")

    assert response.status_code == 200
    assert response.json()["projectBrief"] is None
