import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from controlstudio_host.agent_client import _safe_trusted_context
from controlstudio_host.agent_protocol import AgentProtocolError
from controlstudio_host.api_models import (
    ProjectBriefV1,
    ProjectPlanArtifactV1,
    ProjectRequirementSourceV1,
)
from controlstudio_host.models import MessageRole, RunState
from controlstudio_host.project_planning import (
    append_planning_clarification,
    calculate_project_brief_digest,
    validate_requirement_sources,
)
from controlstudio_host.store import (
    ConversationActiveRunConflict,
    HostStore,
)


class PlanningClarificationIntakeTests(unittest.TestCase):
    def test_exact_source_coverage_resolves_blank_fields_without_editing_them(self):
        fields = ("sequence", "hardware_and_io", "safety_and_interlocks",
                  "operating_modes", "performance_and_acceptance")
        quotes = ("허가 신호에 따라 출력을 켜고 끕니다.", "XGK-CPUE 내부 M 신호를 사용합니다.",
                  "정지 조건이면 모의 출력을 끕니다.", "고정된 단일 운전 모드입니다.",
                  "허가 신호와 모의 출력의 동작 일치를 확인합니다.")
        brief = ProjectBriefV1.model_validate({**self.brief,
            "supplemental_requirements": "\n".join(quotes)})
        sources = [ProjectRequirementSourceV1(field=field, quote=quote)
                   for field, quote in zip(fields, quotes)]
        self.assertEqual(set(validate_requirement_sources(brief, sources, require_complete=True)), set(fields))
        self.assertEqual(brief.hardware_and_io, "")
        with self.assertRaises(ValueError):
            validate_requirement_sources(brief, sources[:-1], require_complete=True)

    def test_rejects_fabricated_stale_duplicate_unknown_and_acknowledgement_quotes(self):
        brief = ProjectBriefV1.model_validate({**self.brief,
            "supplemental_requirements": "실제 원문. CPU는 미정입니다. 네"})
        for quotes in (["invented"], ["이전 답변."], ["CPU는 미정입니다."], ["네"],
                       ["실제 원문.", "실제 원문."]):
            with self.subTest(quotes=quotes), self.assertRaises(ValueError):
                validate_requirement_sources(brief, [ProjectRequirementSourceV1(
                    field="hardware_and_io", quote=quote) for quote in quotes], require_complete=False)

    def test_empty_source_field_does_not_change_legacy_artifact_serialization(self):
        # Serialization compatibility only; not a claim this minimal object is a valid plan.
        legacy = ProjectPlanArtifactV1.model_construct()
        self.assertNotIn("requirementSources", legacy.model_dump(by_alias=True))
        with_source = ProjectPlanArtifactV1.model_construct(requirement_sources=[
            ProjectRequirementSourceV1(field="hardware_and_io", quote="실제 원문.")])
        self.assertEqual(with_source.model_dump(by_alias=True)["requirementSources"], [
            {"field": "hardware_and_io", "quote": "실제 원문."}])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = HostStore(Path(self.directory.name) / "test.sqlite3")
        self.brief = ProjectBriefV1(project_name="모의 인덱스", machine_purpose="PC 모의 운전").model_dump()
        self.conversation = self.store.create_conversation(
            workflow="new_project_planning", project_brief=self.brief)

    def submit(self, content, operation="first", **kwargs):
        return self.store.create_user_message_and_run(
            conversation_id=self.conversation.id, content=content,
            operation_id=operation, workflow="new_project_planning", **kwargs)

    def test_legacy_digest_is_unchanged_when_no_clarification_exists(self):
        legacy = ProjectBriefV1.model_validate(self.brief).model_dump(by_alias=True)
        legacy.pop("supplementalRequirements")
        expected = hashlib.sha256(json.dumps(legacy, ensure_ascii=False, separators=(",", ":"),
                                             sort_keys=True).encode()).hexdigest()
        self.assertEqual(calculate_project_brief_digest(self.brief), expected)

    def test_followup_is_atomic_verbatim_without_mutating_typed_brief(self):
        content = "XG5000과 XG-SIM, XGK-CPUE로 시험해.\n시간 초과는 5초야."
        message, run, created = self.submit(content)
        self.assertTrue(created)
        brief = self.store.get_conversation(self.conversation.id).project_brief
        self.assertEqual(brief["supplemental_requirements"], "")
        self.assertEqual(brief["hardware_and_io"], "")
        self.assertEqual(message.content, content)
        self.assertEqual(calculate_project_brief_digest(brief), calculate_project_brief_digest(self.brief))
        self.assertFalse(self.submit(content)[2])
        self.assertEqual(self.store.get_conversation(self.conversation.id).project_brief, brief)
        self.store.update_run_state(run_id=run.id, state=RunState.COMPLETED)
        second, _, _ = self.submit("6초로 바꿔 줘.", operation="second")
        updated = self.store.get_conversation(self.conversation.id).project_brief
        self.assertEqual(updated, self.brief)
        self.assertEqual(second.content, "6초로 바꿔 줘.")

    def test_system_requests_do_not_become_user_requirements(self):
        self.submit("system continuation", message_role=MessageRole.SYSTEM)
        self.assertEqual(self.store.get_conversation(self.conversation.id).project_brief,
                         self.brief)

    def test_busy_run_does_not_append_an_unaccepted_followup(self):
        self.submit("첫 답변")
        before = self.store.get_conversation(self.conversation.id)
        with self.assertRaises(ConversationActiveRunConflict):
            self.submit("접수되지 않은 답변", operation="second")
        after = self.store.get_conversation(self.conversation.id)
        self.assertEqual(after.project_brief, before.project_brief)
        self.assertEqual(len(after.messages), len(before.messages))

    def test_explicit_form_revision_does_not_duplicate_its_rendered_prompt(self):
        updated = {**self.brief, "supplemental_requirements": "사용자가 편집한 추가 조건"}
        self.submit("폼에서 만든 요청 본문", project_brief=updated,
                    message_metadata={"structured_project_brief": True})
        self.assertEqual(self.store.get_conversation(self.conversation.id).project_brief, updated)

    def test_more_than_20k_of_followup_conversation_does_not_overflow_brief(self):
        for index in range(3):
            message, run, created = self.submit(
                f"상태 확인 {index}: " + "x" * 7_500,
                operation=f"followup-{index}",
            )
            self.assertTrue(created)
            self.assertGreater(len(message.content), 7_500)
            self.store.update_run_state(run_id=run.id, state=RunState.COMPLETED)
        detail = self.store.get_conversation(self.conversation.id)
        self.assertEqual(detail.project_brief, self.brief)
        self.assertGreater(sum(len(message.content) for message in detail.messages), 20_000)

    def test_message_requirement_source_requires_exact_same_conversation_message(self):
        message, _, _ = self.submit("시간 초과는 6초로 바꿔 줘.")
        brief = ProjectBriefV1.model_validate(self.brief)
        source = ProjectRequirementSourceV1(
            field="performance_and_acceptance",
            quote="시간 초과는 6초로 바꿔 줘.",
            message_id=message.id,
        )
        self.assertEqual(
            validate_requirement_sources(
                brief,
                [source],
                require_complete=False,
                requirement_messages={message.id: message.content},
            ),
            {"performance_and_acceptance": source.quote},
        )
        with self.assertRaises(ValueError):
            validate_requirement_sources(
                brief,
                [source],
                require_complete=False,
                requirement_messages={"other-message": message.content},
            )

    def test_host_protocol_preserves_message_requirement_sources(self):
        value = _safe_trusted_context(
            {
                "workflow": "new_project_planning",
                "project_brief": {
                    **self.brief,
                    "requirement_message_sources": [
                        {"message_id": "m1", "content": "6초로 바꿔 줘."}
                    ],
                },
            },
            knowledge_search_available=False,
            change_planner_available=False,
            mne_change_planner_available=False,
        )
        self.assertEqual(
            value["project_brief"]["requirement_message_sources"],
            [{"message_id": "m1", "content": "6초로 바꿔 줘."}],
        )

    def test_host_protocol_preserves_bounded_retained_design_narrative(self):
        narrative = {
            "requirement_sources": [{
                "field": "performance_and_acceptance",
                "quote": "자동과 수동 동작을 확인한다.",
                "message_id": "m1",
            }],
            "status": "ready_for_review",
            "project_name": "인덱스",
            "summary": "기존 검토 서술",
            "objective": "기존 설계를 이어서 완성한다.",
            "user_requirements": ["자동과 수동 동작을 확인한다."],
            "design_proposals": [],
            "scope": [],
            "assumptions": [],
            "open_questions": [],
            "hardware_and_network": [],
            "io_and_devices": [],
            "operating_modes": [],
            "sequence": [],
            "safety_invariants": [],
            "alarms_and_recovery": [],
            "program_structure": [],
            "validation_plan": [],
            "not_applicable": [],
        }
        value = _safe_trusted_context(
            {
                "workflow": "new_project_planning",
                "project_brief": self.brief,
                "retained_design_narrative": narrative,
            },
            knowledge_search_available=False,
            change_planner_available=False,
            mne_change_planner_available=False,
        )
        self.assertEqual(value["retained_design_narrative"], narrative)

        with self.assertRaises(AgentProtocolError):
            _safe_trusted_context(
                {
                    "workflow": "new_project_planning",
                    "project_brief": self.brief,
                    "retained_design_narrative": {
                        **narrative,
                        "unexpected": "must not cross the trusted boundary",
                    },
                },
                knowledge_search_available=False,
                change_planner_available=False,
                mne_change_planner_available=False,
            )

    def test_append_never_silently_truncates_or_mutates_its_input(self):
        original = dict(self.brief)
        self.assertEqual(append_planning_clarification(self.brief, "  원문  ")
                         ["supplemental_requirements"], "  원문  ")
        self.assertEqual(self.brief, original)
        with self.assertRaises(ValidationError):
            append_planning_clarification(self.brief, "x" * 20_001)

    def test_immediately_repeated_clarification_is_idempotent(self):
        once = append_planning_clarification(self.brief, "동일한 추가 조건")
        twice = append_planning_clarification(once, "동일한 추가 조건")

        self.assertEqual(twice, once)
        self.assertEqual(
            twice["supplemental_requirements"],
            "동일한 추가 조건",
        )

    def test_nonconsecutive_retries_are_deduplicated_and_compacted(self):
        existing = {
            **self.brief,
            "supplemental_requirements": "첫 조건\n\n둘째 조건\n\n첫 조건",
        }

        result = append_planning_clarification(existing, "둘째 조건")

        self.assertEqual(
            result["supplemental_requirements"],
            "첫 조건\n\n둘째 조건",
        )


if __name__ == "__main__":
    unittest.main()
