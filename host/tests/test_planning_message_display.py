import unittest

from controlstudio_host.agent_runtime import AgentRuntimeDiagnostic
from controlstudio_host.app import (
    ProjectBriefV1,
    _agent_runtime_user_message,
    _planning_message_display,
    _planning_prompt_from_brief,
    _safe_identity_diagnostic,
)


class PlanningMessageDisplayTests(unittest.TestCase):
    def test_identity_diagnostic_filters_non_codes_and_malformed_metadata(self):
        self.assertEqual(_safe_identity_diagnostic({"metadata": None}), {})
        self.assertEqual(_safe_identity_diagnostic({"metadata": {"strong_identity_diagnostic": []}}), {})
        self.assertEqual(_safe_identity_diagnostic({"metadata": {"strong_identity_diagnostic": {
            "stage": "vendor_project_identity", "code": "SECRET / PATH", "message": "secret",
        }}}), {"stage": "vendor_project_identity"})

    def test_timeout_explains_waiting_without_claiming_no_changes(self):
        message = _agent_runtime_user_message(AgentRuntimeDiagnostic(
            stage="planning_native_design", exception_kind="model_transport",
            reason_code="provider_timeout",
        ))
        self.assertIn("시간이 초과", message)
        self.assertIn("대화에 남아", message)
        self.assertNotIn("연결이 완료되지", message)
        self.assertNotIn("실제 변경은 수행되지", message)

    def setUp(self):
        self.brief = ProjectBriefV1(
            project_name="인덱스 재구성", machine_purpose="시작 후 한 칸 이동합니다.",
            safety_and_interlocks="모의 신호만 사용하고 비상정지 후 자동 재시작하지 않습니다.",
        )

    def test_shows_entered_requirements_not_internal_instructions(self):
        raw = _planning_prompt_from_brief(self.brief)
        visible = _planning_message_display(raw, self.brief.model_dump())
        self.assertIn(self.brief.machine_purpose, visible)
        self.assertIn(self.brief.safety_and_interlocks, visible)
        self.assertNotIn("구조화된 요구사항만", visible)
        self.assertNotIn("자료 ID", visible)
        self.assertNotIn("아직 모름", visible)
        self.assertIn("구조화된 요구사항만", raw)

    def test_different_historical_brief_is_not_rewritten_with_latest_values(self):
        raw = _planning_prompt_from_brief(self.brief)
        changed = self.brief.model_copy(update={"machine_purpose": "다른 요구사항"})
        visible = _planning_message_display(raw, changed.model_dump())
        self.assertIn(self.brief.machine_purpose, visible)
        self.assertNotIn("다른 요구사항", visible)
        self.assertNotIn("구조화된 요구사항만", visible)

    def test_legacy_display_requires_exact_server_template_round_trip(self):
        raw = _planning_prompt_from_brief(self.brief)
        modified = raw.replace("다음은 ControlStudio", "사용자가 쓴 ControlStudio")
        self.assertEqual(_planning_message_display(modified, None), modified)

    def test_free_text_is_not_rewritten(self):
        self.assertEqual(_planning_message_display("추가 질문입니다.", self.brief.model_dump()), "추가 질문입니다.")


if __name__ == "__main__":
    unittest.main()
