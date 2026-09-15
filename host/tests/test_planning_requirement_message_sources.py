from datetime import datetime, timezone

from controlstudio_host.app import _planning_requirement_message_sources
from controlstudio_host.models import Message, MessageRole, ProjectPlanReview

NOW = datetime.now(timezone.utc)


def _message(
    identity: str,
    sequence: int,
    role: MessageRole,
    content: str,
    *,
    artifact=None,
    reviewed=False,
):
    return Message(
        id=identity,
        conversation_id="conversation",
        sequence=sequence,
        role=role,
        content=content,
        metadata={"plan_artifact": artifact} if artifact is not None else {},
        created_at=NOW,
        project_plan_review=(
            ProjectPlanReview(
                schema_version="2",
                conversation_id="conversation",
                message_id=identity,
                plan_digest="a" * 64,
                reviewed_section_ids=["user-requirements"],
                reviewed_item_ids=["item"],
                project_brief_digest="b" * 64,
                attachment_revision_digest="c" * 64,
                actor_id="reviewer",
                reviewed_at=NOW,
            )
            if reviewed
            else None
        ),
    )


def test_unreviewed_requirement_survives_partial_plan_and_later_answer():
    messages = [
        _message("requirement-a", 1, MessageRole.USER, "요구 A"),
        _message(
            "partial-plan",
            2,
            MessageRole.ASSISTANT,
            "추가 질문",
            artifact={"status": "needs_clarification", "requirementSources": []},
        ),
        _message("answer-b", 3, MessageRole.USER, "답변 B"),
    ]
    assert _planning_requirement_message_sources(
        messages, retained_message_ids=set()
    ) == [
        {"message_id": "requirement-a", "content": "요구 A"},
        {"message_id": "answer-b", "content": "답변 B"},
    ]


def test_review_boundary_keeps_cited_and_draft_sources_plus_new_messages():
    reviewed_artifact = {
        "status": "ready_for_review",
        "requirementSources": [
            {"field": "sequence", "quote": "요구 A", "messageId": "requirement-a"}
        ],
    }
    messages = [
        _message("old-status", 1, MessageRole.USER, "상태 알려줘"),
        _message("requirement-a", 2, MessageRole.USER, "요구 A"),
        _message("draft-source", 3, MessageRole.USER, "초안 보존 요구"),
        _message(
            "reviewed-plan",
            4,
            MessageRole.ASSISTANT,
            "검토 계획",
            artifact=reviewed_artifact,
            reviewed=True,
        ),
        _message("new-followup", 5, MessageRole.USER, "새 요구"),
    ]
    assert _planning_requirement_message_sources(
        messages, retained_message_ids={"draft-source"}
    ) == [
        {"message_id": "requirement-a", "content": "요구 A"},
        {"message_id": "draft-source", "content": "초안 보존 요구"},
        {"message_id": "new-followup", "content": "새 요구"},
    ]


def test_narrative_adoption_boundary_replaces_older_review_boundary():
    messages = [
        _message("reviewed-plan", 1, MessageRole.ASSISTANT, "검토 계획", artifact={
            "status": "ready_for_review", "requirementSources": [],
        }, reviewed=True),
        _message("old-technical", 2, MessageRole.USER, "정상 정지 입력을 추가해 줘."),
        _message("narrative-adopted-by", 3, MessageRole.USER, "요구를 반영해 완성해 줘."),
        _message("new-continuation", 4, MessageRole.USER, "이어서 완성해 줘."),
        _message("new-technical", 5, MessageRole.USER, "준비 입력도 연결해 줘."),
    ]

    assert _planning_requirement_message_sources(
        messages,
        retained_message_ids=set(),
        narrative_adoption_boundary_message_id="narrative-adopted-by",
    ) == [
        {"message_id": "new-continuation", "content": "이어서 완성해 줘."},
        {"message_id": "new-technical", "content": "준비 입력도 연결해 줘."},
    ]


def test_missing_narrative_boundary_falls_back_to_review_boundary():
    messages = [
        _message("before-review", 1, MessageRole.USER, "이전 상태 질문"),
        _message("review", 2, MessageRole.ASSISTANT, "검토", artifact={
            "status": "ready_for_review", "requirementSources": [],
        }, reviewed=True),
        _message("after-review", 3, MessageRole.USER, "새 요구"),
    ]
    assert _planning_requirement_message_sources(
        messages,
        retained_message_ids=set(),
        narrative_adoption_boundary_message_id="missing-message",
    ) == [{"message_id": "after-review", "content": "새 요구"}]
