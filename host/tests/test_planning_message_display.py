"""Display server-authored requests without changing their stored/model content."""

import ast
from pathlib import Path

import pytest

from controlstudio_host import app as host
from controlstudio_host.api_models import ProjectBriefV1


@pytest.fixture
def brief():
    return ProjectBriefV1(
        project_name="두 펌프", machine_purpose="두 펌프를 교번 운전합니다.",
        sequence="정지 후 새 기동", hardware_and_io="펌프 두 대",
        safety_and_interlocks="저수위 정지", attachment_source_ids=["manual-1"],
    )


def creation_request(brief):
    # Read the actual request producer's suffix, rather than maintaining a
    # separate test template that could drift from the submitted model input.
    tree = ast.parse(Path(host.__file__).read_text(encoding="utf-8"))
    assignment = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.Assign)
                      and isinstance(n.value, ast.BinOp)
                      and isinstance(n.value.left, ast.Call)
                      and isinstance(n.value.left.func, ast.Attribute)
                      and n.value.left.func.attr == "removesuffix"
                      and isinstance(n.value.left.func.value, ast.Name)
                      and n.value.left.func.value.id == "authoritative_content")
    suffix = ast.literal_eval(assignment.value.right)
    return host._planning_prompt_from_brief(brief).removesuffix(host._LEGACY_PROJECT_BRIEF_FOOTER) + suffix


@pytest.mark.parametrize("create", [False, True])
def test_exact_message_brief_displays_entered_requirements_without_authority_prompt(brief, create):
    stored = creation_request(brief) if create else host._planning_prompt_from_brief(brief)
    original = str(stored)
    result = host._planning_message_display(stored, brief.model_dump())
    assert "서버가 검증" not in result and "권위로 사용" not in result
    assert "두 펌프를 교번 운전합니다." in result
    assert "저수위 정지" in result and "참고 자료 1개 첨부" in result
    assert stored == original
    assert "구조화된 요구사항만 설계 범위의 권위" in stored


@pytest.mark.parametrize("create", [False, True])
def test_different_or_missing_message_brief_does_not_rewrite_historical_content(brief, create):
    stored = creation_request(brief) if create else host._planning_prompt_from_brief(brief)
    changed = brief.model_copy(update={"machine_purpose": "다른 후속 요구"})
    for value in (None, {}, changed.model_dump()):
        assert host._planning_message_display(stored, value) == stored


def test_user_authored_suffix_is_never_removed_as_a_display_template(brief):
    original = host._planning_prompt_from_brief(brief)
    user_text = original + "\n위 내용을 그대로 인용해서 설명해 주세요."
    assert host._planning_message_display(user_text, brief.model_dump()) == user_text
