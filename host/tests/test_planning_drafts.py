from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from controlstudio_host.api_models import (
    ProjectBriefV1,
    ProjectPlanIoPointV1,
    ProjectPlanNativeDesignV1,
)
from controlstudio_host.native_projects import NativeProjectPreconditionError
from controlstudio_host.planning_drafts import (
    ProjectDesignDraft,
    ProjectDesignDraftConflict,
    ProjectDesignDraftRepository,
    ProjectDesignDraftService,
    _INDEX_ACCEPTANCE_INPUT_ROLES,
    _index_mode_latch_repair_changes,
    _index_motion_run_gate_repair_changes,
    _index_timeout_reset_gate_repair_changes,
    _index_sequence_stopped_gate_repair_changes,
    _index_start_rearm_repair_changes,
    _stale_step_reference_repair_changes,
    _control_network_contract_repair_changes,
    _sequence_start_release_contract_repair_changes,
    _index_acceptance_behavior_diagnostics,
    _index_acceptance_binding_diagnostics,
    _reference_diagnostics,
    _requirement_diagnostics,
)


def test_timeout_reset_scan_order_diagnostic_and_scoped_repair() -> None:
    signals = {
        "automatic_start": "start", "manual_start": "manual_start", "stop": "stop",
        "reset": "reset", "select_automatic": "select_auto",
        "select_manual": "select_manual", "emergency_stop": "estop",
        "door_open": "door", "ready": "ready", "move_complete": "complete",
        "motion": "motion", "automatic_mode": "mode_auto",
        "manual_mode": "mode_manual", "running": "running", "moving": "moving",
        "any_alarm": "any_alarm",
    }
    camel_design = {
        "indexAcceptanceBindings": {
            "signals": signals,
            "modes": {"automatic": "auto", "manual": "manual"},
            "alarms": {
                "emergency": "alarm_estop", "door": "alarm_door",
                "timeout": "alarm_timeout",
            },
            "timerId": "move_timeout",
        },
        "alarms": [
            {"alarmId": "alarm_estop", "latchSignalId": "latch_estop"},
            {"alarmId": "alarm_door", "latchSignalId": "latch_door"},
            {"alarmId": "alarm_timeout", "latchSignalId": "latch_timeout"},
        ],
        "operatingModes": [], "sequences": [], "ioPoints": [],
        "recoveryRules": [{
            "recoveryId": "recover_timeout", "resetAlarmIds": ["alarm_timeout"],
            "resetSequenceIds": [], "whenAll": [
                {"signalId": "timeout_done", "state": "off"},
                {"signalId": "reset", "state": "on"},
                {"signalId": "latch_timeout", "state": "on"},
            ],
        }],
        "ladderRungs": [
            {
                "rungId": "timeout_done_out",
                "condition": {"kind": "contact", "sourceType": "timer",
                              "sourceId": "move_timeout", "state": "on"},
                "action": {"instruction": "OUT", "targetType": "signal",
                           "targetId": "timeout_done"},
            },
            {
                "rungId": "timeout_reset",
                "condition": {"kind": "all_of", "terms": [
                    {"kind": "contact", "sourceType": "signal",
                     "sourceId": "timeout_done", "state": "off"},
                    {"kind": "contact", "sourceType": "signal",
                     "sourceId": "reset", "state": "on"},
                    {"kind": "contact", "sourceType": "signal",
                     "sourceId": "latch_timeout", "state": "on"},
                ]},
                "action": {"instruction": "RST", "targetType": "signal",
                           "targetId": "latch_timeout"},
            },
        ],
    }
    diagnostics = _index_acceptance_behavior_diagnostics(camel_design)
    diagnostic = next(
        item for item in diagnostics
        if item["code"] == "index_acceptance_timeout_reset_blocked_by_done_mirror"
    )
    snake_design = {
        "recovery_rules": [{
            "recovery_id": "recover_timeout", "reset_alarm_ids": ["alarm_timeout"],
            "reset_sequence_ids": [], "when_all": [
                {"signal_id": "timeout_done", "state": "off"},
                {"signal_id": "reset", "state": "on"},
                {"signal_id": "latch_timeout", "state": "on"},
            ],
        }],
        "ladder_rungs": [{
            "rung_id": "timeout_reset",
            "condition": {"kind": "all_of", "terms": [
                {"kind": "contact", "source_type": "signal",
                 "source_id": "timeout_done", "state": "off"},
                {"kind": "contact", "source_type": "signal",
                 "source_id": "reset", "state": "on"},
                {"kind": "contact", "source_type": "signal",
                 "source_id": "latch_timeout", "state": "on"},
            ]},
            "action": {"instruction": "RST", "target_type": "signal",
                       "target_id": "latch_timeout"},
        }],
    }
    changes = _index_timeout_reset_gate_repair_changes(snake_design, [diagnostic])
    by_section = {change["section"]: change["value"] for change in changes}
    assert by_section["recovery_rules"]["when_all"] == [
        {"signal_id": "reset", "state": "on"},
        {"signal_id": "latch_timeout", "state": "on"},
    ]
    assert by_section["ladder_rungs"]["condition"]["terms"] == [
        {"kind": "contact", "source_type": "signal",
         "source_id": "reset", "state": "on"},
        {"kind": "contact", "source_type": "signal",
         "source_id": "latch_timeout", "state": "on"},
    ]


def test_summary_projects_bounded_independent_ladder_repairs_together() -> None:
    diagnostics = [
        {
            "code": "index_acceptance_mode_selection_not_retained_until_stopped",
            "path": "/ladderRungs/0/condition",
            "relatedIds": ["mode_auto_out", "mode_manual_out"],
        },
        {
            "code": "index_acceptance_automatic_start_rearm_missing",
            "path": "/ladderRungs/2/condition",
            "relatedIds": ["running_out"],
        },
    ]
    draft = ProjectDesignDraft(
        conversation_id="fixture",
        revision=7,
        brief_digest="b" * 64,
        document={
            "narrative": {},
            "technical_configuration": None,
            "native_design": {
                "ladder_rungs": [
                    {"rung_id": "mode_auto_out"},
                    {"rung_id": "mode_manual_out"},
                    {"rung_id": "running_out"},
                ],
            },
            "remaining_work": [],
        },
        diagnostics=diagnostics,
        status="invalid",
        published_digest=None,
        updated_by_run_id="run",
        updated_at="2026-09-15T00:00:00Z",
    )

    summary = ProjectDesignDraftService._summary(draft)

    assert summary["diagnostics"] == diagnostics
    assert summary["remainingDiagnosticCount"] == 0
    assert len(summary["repairContexts"]) == 2
    assert {item["rung_id"] for item in summary["repairContexts"][0]["items"]} == {
        "mode_auto_out", "mode_manual_out",
    }
    assert summary["repairContexts"][1]["requiredDeclarations"] == [
        {"kind": "internal_bit", "purpose": "automatic_start_release_arm"}
    ]


def test_retained_narrative_is_projected_only_for_the_same_brief() -> None:
    narrative = {"status": "ready_for_review", "project_name": "fixture"}
    draft = ProjectDesignDraft(
        conversation_id="fixture", revision=3, brief_digest="a" * 64,
        document={"narrative": narrative, "native_design": {}}, diagnostics=[],
        status="working", published_digest=None, updated_by_run_id="run",
        updated_at="2026-09-15T00:00:00Z",
    )
    repository = unittest.mock.Mock()
    repository.get.return_value = draft
    service = ProjectDesignDraftService(repository)

    assert service.retained_narrative("fixture", brief_digest="a" * 64) == narrative
    assert service.retained_narrative("fixture", brief_digest="b" * 64) is None


def test_narrative_boundary_uses_last_content_change_not_later_ladder_run() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "host.sqlite3"
        repository = ProjectDesignDraftRepository(database)
        initial = repository.ensure(
            conversation_id="conversation", brief_digest="a" * 64,
            project_name="fixture", run_id="seed-run",
        )
        narrative = {"status": "ready_for_review", "project_name": "fixture"}
        document = copy.deepcopy(initial.document)
        document["narrative"] = narrative
        adopted = repository.update(
            conversation_id="conversation", expected_revision=0,
            brief_digest="a" * 64, document=document, diagnostics=[],
            status="working", published_digest=None, run_id="narrative-run",
        )
        ladder_document = copy.deepcopy(adopted.document)
        ladder_document["native_design"]["ladder_rungs"] = [{"rung_id": "later-rung"}]
        repository.update(
            conversation_id="conversation", expected_revision=1,
            brief_digest="a" * 64, document=ladder_document, diagnostics=[],
            status="working", published_digest=None, run_id="ladder-run",
        )
        connection = sqlite3.connect(database)
        try:
            connection.executescript(
                """
                CREATE TABLE messages(
                    id TEXT PRIMARY KEY, conversation_id TEXT, sequence INTEGER,
                    role TEXT, metadata_json TEXT
                );
                CREATE TABLE runs(id TEXT PRIMARY KEY, conversation_id TEXT, request_message_id TEXT);
                INSERT INTO messages VALUES('narrative-message','conversation',1,'user','{}');
                INSERT INTO messages VALUES('ladder-message','conversation',2,'user','{}');
                INSERT INTO runs VALUES('narrative-run','conversation','narrative-message');
                INSERT INTO runs VALUES('ladder-run','conversation','ladder-message');
                """
            )
            connection.commit()
        finally:
            connection.close()

        assert repository.narrative_adoption_boundary_message_id(
            "conversation", brief_digest="a" * 64, retained_narrative=narrative,
        ) == "narrative-message"
        service = ProjectDesignDraftService(repository)
        drift = service.retained_narrative_context(
            "conversation", brief_digest="b" * 64
        )
        assert drift is not None
        assert drift["brief_relation"] == "supplemental_only_drift"

        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?)",
                ("structured-change", "conversation", 3, "user", '{"structured_project_brief":true}'),
            )
            connection.commit()
        finally:
            connection.close()
        assert service.retained_narrative_context(
            "conversation", brief_digest="b" * 64
        ) is None


def _brief() -> dict[str, object]:
    return {
        "project_name": "모의 출력",
        "machine_purpose": "내부 신호로 출력을 모의 운전한다.",
        "sequence": "허가 신호가 켜지면 출력을 켠다.",
        "hardware_and_io": "XGK-CPUE와 내부 M 신호만 사용한다.",
        "safety_and_interlocks": "허가 신호가 꺼지면 출력도 꺼진다.",
        "operating_modes": "고정 단일 모드로 운전한다.",
        "performance_and_acceptance": "출력 ON/OFF를 확인한다.",
        "supplemental_requirements": "시퀀스, 타이머, 알람은 사용하지 않는다.",
        "attachment_source_ids": [],
    }


def _narrative() -> dict[str, object]:
    return {
        "requirement_sources": [],
        "status": "ready_for_review",
        "project_name": "모의 출력",
        "summary": "내부 신호 기반 모의 출력 설계",
        "objective": "허가 입력에 따라 내부 출력을 제어한다.",
        "user_requirements": ["XGK-CPUE를 사용한다."],
        "design_proposals": ["허가 접점 하나로 출력 코일을 제어한다."],
        "scope": ["내부 M 신호 기반 LD 프로그램"],
        "assumptions": [],
        "open_questions": [],
        "hardware_and_network": ["XGK-CPUE, 통신 구성 없음"],
        "io_and_devices": ["M00000 허가, M00001 출력"],
        "operating_modes": ["고정 단일 모드"],
        "sequence": ["허가 ON이면 출력 ON"],
        "safety_invariants": ["허가 OFF이면 출력 OFF"],
        "alarms_and_recovery": ["알람과 복구 로직 없음"],
        "program_structure": ["MAIN의 단일 네트워크"],
        "validation_plan": ["허가 ON/OFF에 따른 출력 상태 확인"],
        "not_applicable": [],
    }


def _design_changes(*, target_id: str = "run_output") -> list[dict[str, object]]:
    return [
        {"operation": "replace_section", "section": "narrative", "value": _narrative()},
        {
            "operation": "replace_section",
            "section": "technical_configuration",
            "value": {
                "plc_series": "XGK",
                "cpu_model": "XGK-CPUE",
                "program_name": "MAIN",
                "language": "LD",
                "description": "내부 신호 모의 출력",
            },
        },
        {
            "operation": "replace_section",
            "section": "io_points",
            "value": [
                {
                    "signal_id": "permit",
                    "symbol": "PERMIT",
                    "address": "M00000",
                    "kind": "digital_input",
                    "description": "모의 허가 입력",
                    "normal_state": "off",
                    "safe_state": "not_applicable",
                },
                {
                    "signal_id": "run_output",
                    "symbol": "RUN_OUTPUT",
                    "address": "M00001",
                    "kind": "internal_bit",
                    "description": "모의 운전 출력",
                    "normal_state": "off",
                    "safe_state": "off",
                },
            ],
        },
        {"operation": "replace_section", "section": "operating_modes", "value": []},
        {
            "operation": "replace_section",
            "section": "control_networks",
            "value": [
                {
                    "network_id": "run_network",
                    "title": "허가 기반 운전 출력",
                    "mode_id": None,
                    "step_id": None,
                    "permit_all": [{"signal_id": "permit", "state": "on"}],
                    "output_signal_id": "run_output",
                }
            ],
        },
        {
            "operation": "replace_section",
            "section": "ladder_rungs",
            "value": [
                {
                    "rung_id": "run_output_rung",
                    "description": "허가 입력으로 내부 운전 출력을 구동한다.",
                    "condition": {
                        "kind": "contact",
                        "source_type": "signal",
                        "source_id": "permit",
                        "state": "on",
                    },
                    "action": {
                        "instruction": "OUT",
                        "target_type": "signal",
                        "target_id": target_id,
                    },
                }
            ],
        },
        {"operation": "replace_section", "section": "timers", "value": []},
        {"operation": "replace_section", "section": "sequences", "value": []},
        {"operation": "replace_section", "section": "alarms", "value": []},
        {"operation": "replace_section", "section": "recovery_rules", "value": []},
        {"operation": "replace_section", "section": "safety_rules", "value": []},
    ]


class PlanningDraftTests(unittest.TestCase):
    def fake_model_execute(
        self, tool_name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        """Exercise only calls a model could make from the advertised catalog."""

        catalog = {
            item.tool_name: item.parameters_json_schema
            for item in self.service.tool_catalog()
        }
        schema = catalog[tool_name]
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(arguments)
        return self.execute(tool_name, arguments)

    def test_advertised_atomic_edit_persists_checks_and_resumes(self) -> None:
        seed = _design_changes()
        seed[5]["value"][0]["condition"]["source_id"] = "missing"
        saved = self.execute("design.edit", {"expected_revision": 0, "changes": seed})
        invalid = self.execute("design.check", {"expected_revision": saved["revision"]})
        self.assertEqual(invalid["status"], "invalid")
        before = self.service._repository.get("planning-conversation")

        related_batch = [
            {
                "operation": "patch_item",
                "section": "control_networks",
                "item_id": "run_network",
                "value": {"permit_all": [{"signal_id": "permit", "state": "off"}]},
            },
            {
                "operation": "patch_item",
                "section": "ladder_rungs",
                "item_id": "run_output_rung",
                "value": {
                    "condition": {
                        "kind": "contact",
                        "source_type": "signal",
                        "source_id": "permit",
                        "state": "off",
                    }
                },
            },
        ]
        malformed_second = [related_batch[0], {**related_batch[1], "item_id": "missing_rung"}]
        rejected = self.fake_model_execute(
            "design.edit",
            {"expected_revision": before.revision, "changes": malformed_second},
        )
        self.assertEqual(rejected["status"], "not_applied")
        retained = self.service._repository.get("planning-conversation")
        self.assertEqual(retained.revision, before.revision)
        self.assertEqual(retained.document, before.document)

        repaired = self.fake_model_execute(
            "design.edit",
            {"expected_revision": before.revision, "changes": related_batch},
        )
        self.assertEqual(repaired["status"], "applied")
        checked = self.fake_model_execute(
            "design.check", {"expected_revision": repaired["revision"]}
        )
        self.assertEqual(checked["status"], "valid", checked.get("diagnostics"))

        unknown_batch = [
            {
                **related_batch[0],
                "value": {"permit_all": [{"signal_id": "future_permit", "state": "on"}]},
            },
            {
                **related_batch[1],
                "value": {
                    "condition": {
                        "kind": "contact",
                        "source_type": "signal",
                        "source_id": "future_permit",
                        "state": "on",
                    }
                },
            },
        ]
        working = self.fake_model_execute(
            "design.edit",
            {"expected_revision": checked["revision"], "changes": unknown_batch},
        )
        self.assertEqual(working["status"], "applied")
        unknown_check = self.fake_model_execute(
            "design.check", {"expected_revision": working["revision"]}
        )
        self.assertEqual(unknown_check["status"], "invalid")
        blocked = self.fake_model_execute(
            "design.publish_review",
            {"expected_revision": unknown_check["revision"]},
        )
        self.assertNotEqual(blocked["status"], "published")

        restarted = ProjectDesignDraftService(ProjectDesignDraftRepository(self.database_path))
        resumed = asyncio.run(
            restarted.execute(
                tool_name="design.read",
                arguments={"sections": ["control_networks", "ladder_rungs"]},
                **{**self.arguments, "run_id": "run-resumed"},
            )
        )
        self.assertEqual(resumed["revision"], blocked["revision"])
        self.assertEqual(
            resumed["sections"]["control_networks"][0]["permit_all"][0]["signal_id"],
            "future_permit",
        )
        self.assertEqual(
            resumed["sections"]["ladder_rungs"][0]["condition"]["source_id"],
            "future_permit",
        )

    def test_read_current_explicit_revision_and_latest_published_base(self) -> None:
        changes = _design_changes()
        changes[5]["value"].append(
            {
                "rung_id": "counter_init",
                "description": "시험용 데이터 레지스터를 초기화한다.",
                "condition": {
                    "kind": "contact",
                    "source_type": "signal",
                    "source_id": "permit",
                    "state": "off",
                },
                "action": {
                    "instruction": "MOV",
                    "destination_address": "D00100",
                    "source_constant": 0,
                },
            }
        )
        # Seed through the retained legacy bootstrap path; every read/move below
        # is exercised against its currently advertised model contract.
        applied = self.execute(
            "design.edit", {"expected_revision": 0, "changes": changes}
        )
        checked = self.fake_model_execute(
            "design.check", {"expected_revision": applied["revision"]}
        )
        self.assertEqual(checked["status"], "valid", checked.get("diagnostics"))
        published = self.fake_model_execute(
            "design.publish_review", {"expected_revision": checked["revision"]}
        )
        published_revision = published["publishedRevision"]
        moved = self.fake_model_execute(
            "design.move_ladder_rung",
            {
                "expected_revision": published_revision,
                "rung_id": "counter_init",
                "before_rung_id": "run_output_rung",
            },
        )

        current = self.fake_model_execute(
            "design.read", {"sections": ["ladder_rungs"]}
        )
        explicit_base = self.fake_model_execute(
            "design.read",
            {"sections": ["ladder_rungs"], "revision": published_revision},
        )
        published_base = self.fake_model_execute(
            "design.read",
            {"sections": ["ladder_rungs"], "revision": "latest_published"},
        )
        digest_base = self.fake_model_execute(
            "design.read",
            {
                "sections": ["ladder_rungs"],
                "published_plan_digest": published["publishedDigest"],
            },
        )
        self.assertEqual(current["revision"], moved["revision"])
        self.assertEqual(current["currentRevision"], moved["revision"])
        self.assertEqual(current["readRevision"], moved["revision"])
        self.assertEqual(
            [item["rung_id"] for item in current["sections"]["ladder_rungs"]],
            ["counter_init", "run_output_rung"],
        )
        for historical in (explicit_base, published_base, digest_base):
            self.assertEqual(historical["revision"], moved["revision"])
            self.assertEqual(historical["currentRevision"], moved["revision"])
            self.assertEqual(historical["readRevision"], published_revision)
            self.assertEqual(historical["readStatus"], "published")
            self.assertFalse(historical["isCurrentRevision"])
            self.assertEqual(
                [item["rung_id"] for item in historical["sections"]["ladder_rungs"]],
                ["run_output_rung", "counter_init"],
            )
        self.assertEqual(
            self.service._repository.get("planning-conversation").revision,
            moved["revision"],
        )
        with self.assertRaisesRegex(ProjectDesignDraftConflict, "mutually exclusive"):
            self.execute(
                "design.read",
                {
                    "sections": ["ladder_rungs"],
                    "revision": published_revision,
                    "published_plan_digest": published["publishedDigest"],
                },
            )
        missing_digest = self.fake_model_execute(
            "design.read",
            {"sections": ["ladder_rungs"], "published_plan_digest": "f" * 64},
        )
        self.assertEqual(missing_digest["status"], "revision_not_found")
        self.assertEqual(missing_digest["retainedRevision"], moved["revision"])

        other_arguments = {
            **self.arguments,
            "conversation_id": "other-planning-conversation",
            "run_id": "other-run",
        }
        cross_conversation = asyncio.run(
            self.service.execute(
                tool_name="design.read",
                arguments={
                    "sections": ["ladder_rungs"],
                    "published_plan_digest": published["publishedDigest"],
                },
                **other_arguments,
            )
        )
        self.assertEqual(cross_conversation["status"], "revision_not_found")
        self.assertEqual(cross_conversation["retainedRevision"], 0)

        inspection = self.fake_model_execute("design.inspect", {})
        self.assertEqual(
            inspection["revisionAvailability"]["latestPublishedRevision"],
            published_revision,
        )
        self.assertEqual(
            inspection["revisionAvailability"]["maximumRevision"], moved["revision"]
        )

    def test_repeated_rung_move_cannot_cycle_back_to_a_prior_run_order(self) -> None:
        changes = _design_changes()
        changes[5]["value"].append(
            {
                "rung_id": "counter_init",
                "description": "시험용 데이터 레지스터를 초기화한다.",
                "condition": {
                    "kind": "contact", "source_type": "signal",
                    "source_id": "permit", "state": "off",
                },
                "action": {
                    "instruction": "MOV", "destination_address": "D00100",
                    "source_constant": 0,
                },
            }
        )
        applied = self.execute("design.edit", {"expected_revision": 0, "changes": changes})
        self.arguments["run_id"] = "move-run"
        moved = self.fake_model_execute(
            "design.move_ladder_rung",
            {
                "expected_revision": applied["revision"],
                "rung_id": "counter_init",
                "before_rung_id": "run_output_rung",
            },
        )
        cycled = self.fake_model_execute(
            "design.move_ladder_rung",
            {
                "expected_revision": moved["revision"],
                "rung_id": "run_output_rung",
                "before_rung_id": "counter_init",
            },
        )

        self.assertEqual(cycled["status"], "not_applied")
        self.assertEqual(cycled["retainedRevision"], moved["revision"])
        self.assertEqual(cycled["diagnostics"][0]["code"], "ladder_move_repeats_prior_order")
        self.assertEqual(cycled["diagnosticsFreshness"], "retained_not_rechecked")
        self.assertEqual(
            self.service._repository.get("planning-conversation").revision,
            moved["revision"],
        )
        read = self.execute("design.read", {"sections": ["ladder_rungs"]})
        self.assertEqual(
            [item["rung_id"] for item in read["sections"]["ladder_rungs"]],
            ["counter_init", "run_output_rung"],
        )

    def test_current_diagnostics_suppress_advisory_execution_observations(self) -> None:
        applied = self.execute(
            "design.edit",
            {"expected_revision": 0, "changes": _design_changes()},
        )
        working = self.execute("design.inspect", {})["executionObservations"]
        self.assertEqual(working["kind"], "advisory_suppressed_until_valid_check")
        self.assertEqual(working["draftStatus"], "working")

        valid = self.execute("design.check", {"expected_revision": applied["revision"]})
        self.assertEqual(valid["status"], "valid")
        checked_observation = self.execute("design.inspect", {})["executionObservations"]
        self.assertEqual(checked_observation["kind"], "static_dataflow_not_runtime_test")
        self.assertIn("potentialResetOrderDependencies", checked_observation)

        changed = self.execute(
            "design.edit_ladder_rungs",
            {
                "expected_revision": valid["revision"],
                "changes": [{
                    "operation": "patch_item",
                    "item_id": "run_output_rung",
                    "value": {
                        "action": {
                            "instruction": "OUT", "target_type": "signal",
                            "target_id": "missing",
                        }
                    },
                }],
            },
        )
        after_edit = self.execute("design.read", {"sections": ["ladder_rungs"]})[
            "executionObservations"
        ]
        self.assertEqual(after_edit["kind"], "advisory_suppressed_until_valid_check")
        self.assertEqual(after_edit["diagnosticCount"], 0)

        invalid = self.execute("design.check", {"expected_revision": changed["revision"]})
        self.assertEqual(invalid["status"], "invalid")

        for observation in (
            self.execute("design.inspect", {})["executionObservations"],
            self.execute("design.read", {"sections": ["ladder_rungs"]})[
                "executionObservations"
            ],
        ):
            self.assertEqual(
                observation["kind"],
                "advisory_suppressed_until_valid_check",
            )
            self.assertNotIn("potentialResetOrderDependencies", observation)
            self.assertIn("Run design.check", observation["instruction"])

    def test_historical_read_cannot_escape_conversation_or_mutate_current(self) -> None:
        self.execute("design.inspect", {})
        other_arguments = {
            **self.arguments,
            "conversation_id": "other-planning-conversation",
            "run_id": "other-run",
        }
        other = asyncio.run(
            self.service.execute(
                tool_name="design.read",
                arguments={"sections": ["ladder_rungs"], "revision": 1},
                **other_arguments,
            )
        )
        self.assertEqual(other["status"], "revision_not_found")
        self.assertEqual(other["currentRevision"], 0)
        self.assertIn("This planning conversation", other["message"])
        self.assertEqual(
            self.service._repository.get("other-planning-conversation").revision, 0
        )

    def test_index_acceptance_semantics_match_runner_role_contract(self) -> None:
        input_roles = {
            "automatic_start", "manual_start", "stop", "reset", "select_automatic",
            "select_manual", "emergency_stop", "door_open", "ready", "move_complete",
        }
        output_roles = {
            "motion", "automatic_mode", "manual_mode", "running", "moving", "any_alarm",
        }
        alarm_roles = {"emergency", "door", "timeout"}
        signals = {
            role: f"signal_{index}"
            for index, role in enumerate(sorted(input_roles | output_roles))
        }
        alarm_bindings = {
            role: f"alarm_{index}" for index, role in enumerate(sorted(alarm_roles))
        }
        profile = {
            "schemaVersion": "controlstudio-index-acceptance-bindings/v1",
            "signals": signals,
            "modes": {"automatic": "auto", "manual": "manual"},
            "alarms": alarm_bindings,
            "timerId": "timeout",
        }
        design = {
            "ioPoints": [
                {
                    "signalId": identity,
                    "kind": "digital_input" if role in input_roles else "internal_bit",
                }
                for role, identity in signals.items()
            ] + [
                {"signalId": f"latch_{index}", "kind": "internal_bit"}
                for index in range(3)
            ],
            "operatingModes": [{"modeId": value} for value in ("auto", "manual", "stopped")],
            "sequences": [
                {"sequenceId": "auto_sequence", "modeId": "auto"},
                {"sequenceId": "manual_sequence", "modeId": "manual"},
            ],
            "alarms": [
                {"alarmId": identity, "latchSignalId": f"latch_{index}"}
                for index, identity in enumerate(alarm_bindings.values())
            ],
            "timers": [
                {"timerId": "timeout", "durationMs": 5000, "instruction": "TON"}
            ],
            "indexAcceptanceBindings": profile,
        }
        self.assertEqual(_index_acceptance_binding_diagnostics(design), [])

        signals["ready"] = signals["automatic_mode"]
        signals["stop"] = signals["emergency_stop"]
        diagnostics = _index_acceptance_binding_diagnostics(design)
        by_code = {item["code"]: item for item in diagnostics}
        incompatible = by_code["index_acceptance_signal_role_incompatible"]
        self.assertEqual(
            incompatible["path"], "/indexAcceptanceBindings/signals/ready"
        )
        self.assertEqual(incompatible["received"]["kind"], "internal_bit")
        self.assertEqual(incompatible["expectedType"], "digital_input")
        self.assertEqual(incompatible["relatedIds"], [signals["ready"]])
        alias = by_code["index_acceptance_signal_roles_alias"]
        self.assertEqual(
            alias["received"]["roles"], ["emergency_stop", "stop"]
        )

    def test_index_acceptance_behavior_rejects_static_restart_and_interlock_counterexamples(self) -> None:
        signals = {
            "automatic_start": "start",
            "manual_start": "manual_start",
            "stop": "stop",
            "reset": "reset",
            "select_automatic": "select_auto",
            "select_manual": "select_manual",
            "emergency_stop": "estop",
            "door_open": "door",
            "ready": "ready",
            "move_complete": "complete",
            "motion": "motion",
            "automatic_mode": "mode_auto",
            "manual_mode": "mode_manual",
            "running": "running",
            "moving": "moving",
            "any_alarm": "any_alarm",
        }

        def contact(signal_id: str, state: str = "on") -> dict[str, str]:
            return {
                "kind": "contact", "sourceType": "signal",
                "sourceId": signal_id, "state": state,
            }

        def state(signal_id: str, value: str = "on") -> dict[str, str]:
            return {"signalId": signal_id, "state": value}

        def all_of(*terms: dict[str, object]) -> dict[str, object]:
            return {"kind": "all_of", "terms": list(terms)}

        def any_of(*terms: dict[str, object]) -> dict[str, object]:
            return {"kind": "any_of", "terms": list(terms)}

        def out(rung_id: str, target: str, condition: dict[str, object]) -> dict[str, object]:
            return {
                "rungId": rung_id,
                "condition": condition,
                "action": {"instruction": "OUT", "targetType": "signal", "targetId": target},
            }

        auto_condition = all_of(
            contact("mode_manual", "off"),
            any_of(
                all_of(contact("select_auto"), contact("running", "off")),
                all_of(
                    contact("mode_auto"),
                    any_of(contact("running"), contact("select_manual", "off")),
                ),
            ),
        )
        manual_condition = all_of(
            contact("mode_auto", "off"),
            any_of(
                all_of(contact("select_manual"), contact("running", "off")),
                all_of(
                    contact("mode_manual"),
                    any_of(contact("running"), contact("select_auto", "off")),
                ),
            ),
        )
        design = {
            "indexAcceptanceBindings": {
                "signals": signals,
                "modes": {"automatic": "auto", "manual": "manual"},
                "alarms": {"emergency": "alarm_estop", "door": "alarm_door", "timeout": "alarm_timeout"},
                "timerId": "timeout",
            },
            "ioPoints": [
                {"signalId": identity, "kind": "digital_input" if role in _INDEX_ACCEPTANCE_INPUT_ROLES else "internal_bit"}
                for role, identity in signals.items()
            ] + [
                {"signalId": identity, "kind": "internal_bit"}
                for identity in ("start_armed", "latch_estop", "latch_door", "latch_timeout")
            ],
            "operatingModes": [
                {"modeId": "auto", "allowedTransitions": ["stopped"]},
                {"modeId": "manual", "allowedTransitions": ["stopped"]},
                {"modeId": "stopped", "allowedTransitions": ["auto", "manual"]},
            ],
            "alarms": [
                {"alarmId": "alarm_estop", "latchSignalId": "latch_estop", "triggerAll": [state("estop", "off")]},
                {"alarmId": "alarm_door", "latchSignalId": "latch_door", "triggerAll": [state("door")]},
                {"alarmId": "alarm_timeout", "latchSignalId": "latch_timeout", "triggerAll": []},
            ],
            "recoveryRules": [
                {"recoveryId": "recover_estop", "resetAlarmIds": ["alarm_estop"], "whenAll": [state("estop"), state("reset")]},
                {"recoveryId": "recover_door", "resetAlarmIds": ["alarm_door"], "whenAll": [state("door", "off"), state("reset")]},
                {"recoveryId": "recover_timeout", "resetAlarmIds": ["alarm_timeout"], "whenAll": [state("reset")]},
            ],
            "ladderRungs": [
                out("mode_auto_out", "mode_auto", auto_condition),
                out("mode_manual_out", "mode_manual", manual_condition),
                out("motion_out", "motion", all_of(contact("stop", "off"), contact("running"))),
                out(
                    "running_out",
                    "running",
                    all_of(
                        contact("stop", "off"),
                        contact("mode_auto"),
                        any_of(
                            all_of(contact("start"), contact("start_armed")),
                            contact("running"),
                        ),
                    ),
                ),
                out("start_release_arm", "start_armed", contact("start", "off")),
                out("moving_out", "moving", all_of(contact("motion"), contact("stop", "off"))),
                out("any_alarm_out", "any_alarm", any_of(contact("latch_estop"), contact("latch_door"), contact("latch_timeout"))),
            ],
        }

        self.assertEqual(_index_acceptance_behavior_diagnostics(design), [])

        blocked_recovery = copy.deepcopy(design)
        blocked_recovery["ioPoints"].append({"signalId": "stopped", "kind": "internal_bit"})
        blocked_recovery["ladderRungs"].append(out("stopped_out", "stopped", contact("any_alarm", "off")))
        blocked_recovery["recoveryRules"][0]["whenAll"].append(state("stopped"))
        diagnostic = _index_acceptance_behavior_diagnostics(blocked_recovery)[0]
        self.assertEqual(diagnostic["code"], "index_acceptance_recovery_blocked_by_alarm_state")
        self.assertEqual(diagnostic["path"], "/recoveryRules/0/whenAll")

        unguarded_stop = copy.deepcopy(design)
        unguarded_stop["ladderRungs"][2]["condition"] = contact("ready")
        by_code = {item["code"]: item for item in _index_acceptance_behavior_diagnostics(unguarded_stop)}
        self.assertEqual(by_code["index_acceptance_normal_stop_not_enforced"]["path"], "/indexAcceptanceBindings/signals/stop")

        unguarded_running = copy.deepcopy(design)
        unguarded_running["sequences"] = [
            {
                "sequenceId": "seq_auto", "modeId": "auto",
                "steps": [{"stepId": "auto_motion", "completeAll": []}],
            },
            {
                "sequenceId": "seq_manual", "modeId": "manual",
                "steps": [{"stepId": "manual_motion", "completeAll": []}],
            },
        ]
        unguarded_running["ladderRungs"][2]["condition"] = all_of(
            contact("stop", "off"),
            any_of(
                {"kind": "contact", "sourceType": "step", "sourceId": "auto_motion", "state": "on"},
                {"kind": "contact", "sourceType": "step", "sourceId": "manual_motion", "state": "on"},
            ),
        )
        motion_diagnostics = _index_acceptance_behavior_diagnostics(unguarded_running)
        by_code = {item["code"]: item for item in motion_diagnostics}
        motion_gate = by_code["index_acceptance_motion_not_gated_by_running"]
        self.assertEqual(motion_gate["path"], "/ladderRungs/2/condition")

        lost_mode = copy.deepcopy(design)
        lost_mode["ladderRungs"][0]["condition"] = all_of(auto_condition, contact("select_auto"))
        by_code = {item["code"]: item for item in _index_acceptance_behavior_diagnostics(lost_mode)}
        self.assertEqual(by_code["index_acceptance_mode_selection_not_retained_until_stopped"]["path"], "/ladderRungs/0/condition")
        self.assertIn(
            "mode_auto_out",
            by_code["index_acceptance_mode_selection_not_retained_until_stopped"]["relatedIds"],
        )

        level_start = copy.deepcopy(design)
        level_start["ladderRungs"][3]["condition"] = all_of(
            contact("stop", "off"),
            contact("mode_auto"),
            any_of(contact("start"), contact("running")),
        )
        by_code = {item["code"]: item for item in _index_acceptance_behavior_diagnostics(level_start)}
        self.assertEqual(by_code["index_acceptance_automatic_start_rearm_missing"]["path"], "/ladderRungs/3/condition")
        rearm = by_code["index_acceptance_automatic_start_rearm_missing"]
        self.assertIn("running_out", rearm["relatedIds"])
        self.assertEqual(rearm["received"]["requiredRepair"]["declarationKind"], "internal_bit")

        def snake(value: object) -> object:
            if isinstance(value, list):
                return [snake(item) for item in value]
            if not isinstance(value, dict):
                return value
            return {
                re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower(): snake(item)
                for key, item in value.items()
            }

        def camel(value: object) -> object:
            if isinstance(value, list):
                return [camel(item) for item in value]
            if not isinstance(value, dict):
                return value
            result = {}
            binding_map = {"signals", "modes", "alarms"} if {
                "signals", "modes", "timer_id"
            } <= set(value) else set()
            for key, item in value.items():
                public_key = key.split("_")[0] + "".join(
                    part.capitalize() for part in key.split("_")[1:]
                )
                result[public_key] = (
                    copy.deepcopy(item) if key in binding_map else camel(item)
                )
            return result

        motion_document = {"native_design": snake(unguarded_running), "remaining_work": []}
        repaired_motion = ProjectDesignDraftService._apply_changes(
            motion_document,
            _index_motion_run_gate_repair_changes(
                motion_document["native_design"], motion_diagnostics
            ),
        )
        self.assertNotIn(
            "index_acceptance_motion_not_gated_by_running",
            {
                item["code"] for item in _index_acceptance_behavior_diagnostics(
                    camel(repaired_motion["native_design"])
                )
            },
        )
        repaired_condition = next(
            rung for rung in repaired_motion["native_design"]["ladder_rungs"]
            if rung["rung_id"] == "motion_out"
        )["condition"]
        self.assertEqual(
            json.dumps(repaired_condition, sort_keys=True).count('"source_id": "running"'),
            1,
        )

        blocked_sequence = copy.deepcopy(design)
        next(
            item for item in blocked_sequence["ioPoints"]
            if item["signalId"] == "mode_auto"
        )["description"] = "자동 모드 상태 - 정지 모드일 때만 선택 가능"
        next(
            item for item in blocked_sequence["ioPoints"]
            if item["signalId"] == "mode_manual"
        )["description"] = "수동 모드 상태 - 정지 모드일 때만 선택 가능"
        blocked_sequence["ioPoints"].append({
            "signalId": "mode_stopped", "kind": "internal_bit",
            "symbol": "MODE_STOPPED", "description": "자동 수동 정지 모드",
        })
        for mode in blocked_sequence["operatingModes"]:
            if mode["modeId"] in {"auto", "manual"}:
                mode["activation"] = state("mode_stopped")
        blocked_sequence["sequences"] = [
            {
                "sequenceId": "seq_auto", "modeId": "auto",
                "startAll": [state("mode_auto"), state("mode_stopped")],
                "steps": [
                    {"stepId": "auto_wait", "completeAll": [state("ready"), state("mode_stopped")]},
                    {"stepId": "auto_move", "completeAll": [state("complete"), state("mode_stopped")]},
                ],
            },
            {
                "sequenceId": "seq_manual", "modeId": "manual",
                "startAll": [state("mode_manual"), state("mode_stopped")],
                "steps": [
                    {"stepId": "manual_wait", "completeAll": [state("manual_start"), state("mode_stopped")]},
                    {"stepId": "manual_move", "completeAll": [state("complete"), state("mode_stopped")]},
                ],
            },
        ]
        blocked_sequence["ladderRungs"].extend([
            out(
                "mode_stopped_out", "mode_stopped",
                all_of(contact("mode_auto", "off"), contact("mode_manual", "off")),
            ),
            {
                "rungId": "auto_start_step",
                "condition": all_of(
                    {"kind": "contact", "sourceType": "sequence_reset", "sourceId": "seq_auto", "state": "on"},
                    contact("mode_auto"), contact("running"), contact("mode_stopped")
                ),
                "action": {"instruction": "SET", "targetType": "step", "targetId": "auto_wait"},
            },
            {
                "rungId": "auto_move_step",
                "condition": all_of(
                    {"kind": "contact", "sourceType": "step", "sourceId": "auto_wait", "state": "on"},
                    contact("mode_stopped"),
                ),
                "action": {"instruction": "SET", "targetType": "step", "targetId": "auto_move"},
            },
            {
                "rungId": "auto_complete_reset",
                "condition": all_of(
                    {"kind": "contact", "sourceType": "step", "sourceId": "auto_move", "state": "on"},
                    contact("mode_stopped"),
                ),
                "action": {"instruction": "SET", "targetType": "sequence_reset", "targetId": "seq_auto"},
            },
            {
                "rungId": "manual_start_step",
                "condition": all_of(
                    {"kind": "contact", "sourceType": "sequence_reset", "sourceId": "seq_manual", "state": "on"},
                    contact("mode_manual"), contact("manual_start"), contact("mode_stopped")
                ),
                "action": {"instruction": "SET", "targetType": "step", "targetId": "manual_wait"},
            },
            {
                "rungId": "manual_move_step",
                "condition": all_of(
                    {"kind": "contact", "sourceType": "step", "sourceId": "manual_wait", "state": "on"},
                    contact("mode_stopped"),
                ),
                "action": {"instruction": "SET", "targetType": "step", "targetId": "manual_move"},
            },
            {
                "rungId": "manual_complete_reset",
                "condition": all_of(
                    {"kind": "contact", "sourceType": "step", "sourceId": "manual_move", "state": "on"},
                    contact("mode_stopped"),
                ),
                "action": {"instruction": "SET", "targetType": "sequence_reset", "targetId": "seq_manual"},
            },
        ])
        blocked_diagnostics = _index_acceptance_behavior_diagnostics(blocked_sequence)
        deadlock = next(
            item for item in blocked_diagnostics
            if item["code"] == "index_acceptance_active_sequence_blocked_by_stopped_signal"
        )
        self.assertEqual(
            {item["rungId"] for item in deadlock["received"]["blockedRungs"]},
            {
                "auto_start_step", "auto_move_step", "auto_complete_reset",
                "manual_start_step", "manual_move_step", "manual_complete_reset",
            },
        )
        blocked_document = {
            "native_design": snake(blocked_sequence), "remaining_work": []
        }
        repaired_sequence = ProjectDesignDraftService._apply_changes(
            blocked_document,
            _index_sequence_stopped_gate_repair_changes(
                blocked_document["native_design"], blocked_diagnostics
            ),
        )
        repaired_sequence_public = camel(repaired_sequence["native_design"])
        self.assertNotIn(
            "index_acceptance_active_sequence_blocked_by_stopped_signal",
            {
                item["code"]
                for item in _index_acceptance_behavior_diagnostics(
                    repaired_sequence_public
                )
            },
        )
        stopped_writer = next(
            rung for rung in repaired_sequence_public["ladderRungs"]
            if rung["rungId"] == "mode_stopped_out"
        )
        self.assertEqual(stopped_writer["action"]["targetId"], "mode_stopped")
        self.assertFalse(any(
            item.get("signalId") == "mode_stopped"
            for sequence in repaired_sequence_public["sequences"]
            for item in [
                *(sequence.get("startAll") or []),
                *[
                    condition
                    for step in sequence.get("steps") or []
                    for condition in step.get("completeAll") or []
                ],
            ]
        ))
        repaired_modes_by_id = {
            item["modeId"]: item for item in repaired_sequence_public["operatingModes"]
        }
        self.assertEqual(
            repaired_modes_by_id["auto"]["activation"], state("mode_auto")
        )
        self.assertEqual(
            repaired_modes_by_id["manual"]["activation"], state("mode_manual")
        )

        actual_three_mode = copy.deepcopy(lost_mode)
        for item, name, activation_id in zip(
            actual_three_mode["operatingModes"],
            ("자동 모드", "수동 모드", "자동 수동 정지 모드"),
            ("mode_stopped", "mode_stopped", "mode_auto"),
            strict=True,
        ):
            item.update({
                "name": name,
                "description": name,
                "activation": state(activation_id),
            })
        actual_three_mode["ioPoints"].append({
            "signalId": "mode_stopped", "kind": "internal_bit",
            "symbol": "MODE_STOPPED", "description": "자동 수동 정지 모드",
        })
        actual_three_mode["ladderRungs"].insert(2, out(
            "rung_mode_stopped",
            "mode_stopped",
            any_of(
                all_of(contact("mode_auto", "off"), contact("mode_manual", "off")),
                contact("mode_stopped"),
            ),
        ))
        actual_three_mode.setdefault("controlNetworks", [])
        actual_three_mode.setdefault("timers", [])
        actual_three_mode.setdefault("sequences", [])
        actual_three_mode.setdefault("safetyRules", [])
        mode_brief = ProjectBriefV1.model_validate({
            **_brief(),
            "operating_modes": (
                "자동과 수동 모드는 상호 배타이며 정지 중에만 변경한다."
            ),
        })
        before_codes = {
            item["code"] for item in _requirement_diagnostics(
                actual_three_mode, mode_brief
            )
        }
        self.assertIn(
            "mode_activation_start_missing_stopped_condition", before_codes
        )
        three_mode_document = {
            "native_design": snake(actual_three_mode), "remaining_work": []
        }
        live_diagnostics = [
            {
                "code": "mode_activation_start_missing_stopped_condition",
                "path": "/ladderRungs/2/condition",
                "message": "Mode activation requires the stopped condition.",
            },
            {
                "code": "mode_activation_start_missing_stopped_condition",
                "path": "/ladderRungs/2/condition",
                "message": "Mode activation requires the stopped condition.",
            },
        ]
        repaired_three_mode = ProjectDesignDraftService._apply_changes(
            three_mode_document,
            _index_mode_latch_repair_changes(
                three_mode_document["native_design"], live_diagnostics
            ),
        )
        stopped_condition = next(
            rung["condition"]
            for rung in repaired_three_mode["native_design"]["ladder_rungs"]
            if rung["rung_id"] == "rung_mode_stopped"
        )
        self.assertTrue(
            any(
                term.get("source_id") == signals["running"] and term.get("state") == "off"
                for branch in stopped_condition["terms"]
                if branch.get("kind") == "all_of"
                for term in branch.get("terms", [])
            )
        )
        repaired_three_mode_public = camel(repaired_three_mode["native_design"])
        after_codes = {
            item["code"] for item in _requirement_diagnostics(
                repaired_three_mode_public, mode_brief
            )
        } | {
            item["code"] for item in _index_acceptance_behavior_diagnostics(
                repaired_three_mode_public
            )
        }
        self.assertNotIn(
            "index_acceptance_mode_selection_not_retained_until_stopped",
            after_codes,
        )
        self.assertNotIn(
            "mode_activation_start_missing_stopped_condition", after_codes
        )

        lost_document = {"native_design": snake(lost_mode), "remaining_work": []}
        repaired_modes = ProjectDesignDraftService._apply_changes(
            lost_document,
            _index_mode_latch_repair_changes(lost_document["native_design"]),
        )
        repaired_mode_diagnostics = _index_acceptance_behavior_diagnostics(
            camel(repaired_modes["native_design"])
        )
        self.assertNotIn(
            "index_acceptance_mode_selection_not_retained_until_stopped",
            {item["code"] for item in repaired_mode_diagnostics},
            repaired_mode_diagnostics,
        )

        start_document = {"native_design": snake(level_start), "remaining_work": []}
        repaired_start = ProjectDesignDraftService._apply_changes(
            start_document,
            _index_start_rearm_repair_changes(start_document["native_design"]),
        )
        self.assertNotIn(
            "index_acceptance_automatic_start_rearm_missing",
            {
                item["code"]
                for item in _index_acceptance_behavior_diagnostics(
                    camel(repaired_start["native_design"])
                )
            },
        )

    def test_index_scoped_repair_tools_have_minimal_revision_only_schemas(self) -> None:
        catalog = {item.tool_name: item.parameters_json_schema for item in self.service.tool_catalog()}
        for name in (
            "design.repair_index_mode_latches",
            "design.repair_automatic_start_rearm",
            "design.repair_index_sequence_stopped_gates",
            "design.repair_index_motion_run_gate",
            "design.repair_stale_step_references",
            "design.repair_control_network_contracts",
            "design.repair_sequence_start_release_contracts",
        ):
            schema = catalog[name]
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate({"expected_revision": 7})
            self.assertEqual(schema["required"], ["expected_revision"])
            self.assertLess(len(json.dumps(schema, separators=(",", ":"))), 300)

    def test_stale_step_and_network_repairs_preserve_unrelated_ladder_semantics(self) -> None:
        contact = lambda identity: {
            "kind": "contact", "source_type": "step", "source_id": identity,
            "state": "on",
        }
        stale_design = {
            "sequences": [{"sequence_id": "seq_manual", "steps": [
                {"step_id": "step_manual_cmd"}, {"step_id": "step_release"},
            ]}],
            "ladder_rungs": [
                {
                    "rung_id": "stale_transition",
                    "condition": {"kind": "all_of", "terms": [
                        contact("step_removed"),
                        {"kind": "contact", "source_type": "signal",
                         "source_id": "complete", "state": "on"},
                    ]},
                    "action": {"instruction": "SET", "target_type": "step",
                               "target_id": "step_release"},
                },
                {
                    "rung_id": "retained_transition", "condition": contact("step_manual_cmd"),
                    "action": {"instruction": "SET", "target_type": "step",
                               "target_id": "step_release"},
                },
                {
                    "rung_id": "motion_out",
                    "condition": {"kind": "any_of", "terms": [
                        contact("step_manual_cmd"), contact("step_removed"),
                    ]},
                    "action": {"instruction": "OUT", "target_type": "signal",
                               "target_id": "motion"},
                },
            ],
        }
        stale_diagnostics = [
            {"code": "unknown_reference", "expectedType": "step",
             "received": "step_removed", "path": "/ladderRungs/0/condition/terms/0/sourceId"},
            {"code": "unknown_reference", "expectedType": "step",
             "received": "step_removed", "path": "/ladderRungs/2/condition/terms/1/sourceId"},
        ]
        changes = _stale_step_reference_repair_changes(stale_design, stale_diagnostics)
        self.assertEqual(changes[0]["operation"], "remove_item")
        self.assertEqual(changes[0]["item_id"], "stale_transition")
        self.assertEqual(changes[1]["value"]["condition"], contact("step_manual_cmd"))

        network_design = {
            "io_points": [
                {"signal_id": identity, "kind": "internal_bit"}
                for identity in ("arm", "ready", "mode_auto", "mode_manual", "alarm")
            ],
            "control_networks": [
                {"network_id": "cn_mode_auto", "title": "auto",
                 "output_signal_id": "mode_auto", "permit_all": [
                     {"signal_id": "mode_manual", "state": "off"},
                     {"signal_id": "alarm", "state": "off"},
                 ]},
            ],
            "ladder_rungs": [
                {"rung_id": "arm_out", "condition": {
                    "kind": "contact", "source_type": "signal",
                    "source_id": "start", "state": "off"},
                 "action": {"instruction": "OUT", "target_type": "signal", "target_id": "arm"}},
                {"rung_id": "ready_out", "condition": {
                    "kind": "contact", "source_type": "signal",
                    "source_id": "ready_input", "state": "on"},
                 "action": {"instruction": "OUT", "target_type": "signal", "target_id": "ready"}},
                {"rung_id": "mode_auto_out", "condition": {
                    "kind": "any_of", "terms": [
                        {"kind": "contact", "source_type": "signal", "source_id": "select", "state": "on"},
                        {"kind": "contact", "source_type": "signal", "source_id": "mode_auto", "state": "on"},
                    ]},
                 "action": {"instruction": "OUT", "target_type": "signal", "target_id": "mode_auto"}},
            ],
        }
        network_diagnostics = [
            {"code": "control_network_out_cardinality_mismatch", "received": {
                "networkIds": [], "outRungIds": ["arm_out"], "outputSignalId": "arm"}},
            {"code": "control_network_out_cardinality_mismatch", "received": {
                "networkIds": [], "outRungIds": ["ready_out"], "outputSignalId": "ready"}},
            {"code": "control_network_out_missing_effective_permits",
             "path": "/controlNetworks/0/permitAll"},
        ]
        network_changes = _control_network_contract_repair_changes(
            network_design, network_diagnostics
        )
        self.assertEqual(len(network_changes), 3)
        patched = network_changes[-1]["value"]["condition"]
        self.assertEqual(patched["terms"][0]["kind"], "any_of")
        self.assertEqual(
            {(term["source_id"], term["state"]) for term in patched["terms"][1:]},
            {("mode_manual", "off"), ("alarm", "off")},
        )

        release_design = {
            "index_acceptance_bindings": {
                "signals": {
                    "automatic_start": "start", "manual_start": "manual_btn",
                    "running": "running",
                },
                "modes": {"automatic": "auto", "manual": "manual"},
            },
            "io_points": [
                {"signal_id": identity} for identity in ("start", "manual_btn", "running")
            ],
            "sequences": [
                {"sequence_id": "seq_auto", "mode_id": "auto",
                 "start_all": [], "reset_all": []},
                {"sequence_id": "seq_manual", "mode_id": "manual",
                 "start_all": [], "reset_all": []},
            ],
        }
        release_changes = _sequence_start_release_contract_repair_changes(
            release_design,
            [{"code": "sequence_reset_release_missing"}],
        )
        by_sequence = {change["item_id"]: change["value"] for change in release_changes}
        self.assertEqual(
            by_sequence["seq_auto"],
            {"start_all": [{"signal_id": "running", "state": "on"}],
             "reset_all": [{"signal_id": "running", "state": "off"}]},
        )
        self.assertEqual(
            by_sequence["seq_manual"],
            {"start_all": [{"signal_id": "manual_btn", "state": "on"}],
             "reset_all": [{"signal_id": "manual_btn", "state": "off"}]},
        )


    def test_failed_index_scoped_repair_retains_exact_revision_and_document(self) -> None:
        seeded = self.execute(
            "design.edit", {"expected_revision": 0, "changes": _design_changes()}
        )
        draft = self.service._repository.get("planning-conversation")
        self.service._repository.update(
            conversation_id="planning-conversation",
            expected_revision=seeded["revision"],
            brief_digest=draft.brief_digest,
            document=draft.document,
            diagnostics=[{
                "code": "mode_activation_start_missing_stopped_condition",
                "path": "/ladderRungs/0/condition",
                "message": "Mode activation requires the stopped condition.",
            }],
            status="invalid",
            published_digest=None,
            run_id="run-one",
        )
        before = self.service._repository.get("planning-conversation")

        result = self.fake_model_execute(
            "design.repair_index_mode_latches",
            {"expected_revision": before.revision},
        )

        after = self.service._repository.get("planning-conversation")
        self.assertEqual(result["status"], "not_applied")
        self.assertEqual(result["retainedRevision"], before.revision)
        self.assertEqual(after.revision, before.revision)
        self.assertEqual(after.document, before.document)

    def test_design_check_and_publish_reject_configured_incompatible_profile(self) -> None:
        changes = _design_changes()
        input_roles = {
            "automatic_start", "manual_start", "stop", "reset", "select_automatic",
            "select_manual", "emergency_stop", "door_open", "ready", "move_complete",
        }
        output_roles = {
            "motion", "automatic_mode", "manual_mode", "running", "moving", "any_alarm",
        }
        signal_roles = {role: "permit" for role in input_roles}
        signal_roles.update({role: "run_output" for role in output_roles})
        signal_roles["ready"] = "run_output"
        changes.append(
            {
                "operation": "replace_section",
                "section": "index_acceptance_bindings",
                "value": {
                    "signals": signal_roles,
                    "modes": {"automatic": "auto", "manual": "manual"},
                    "alarms": {
                        "emergency": "estop",
                        "door": "door",
                        "timeout": "timeout",
                    },
                    "timer_id": "timeout",
                },
            }
        )
        applied = self.execute(
            "design.edit", {"expected_revision": 0, "changes": changes}
        )
        checked = self.execute(
            "design.check", {"expected_revision": applied["revision"]}
        )
        self.assertEqual(checked["status"], "invalid")
        ready = next(
            item
            for item in checked["diagnostics"]
            if item["path"] == "/indexAcceptanceBindings/signals/ready"
        )
        self.assertEqual(ready["received"]["signalId"], "run_output")
        self.assertEqual(ready["expectedType"], "digital_input")
        blocked = self.execute(
            "design.publish_review", {"expected_revision": checked["revision"]}
        )
        self.assertEqual(blocked["status"], "invalid")

    def test_index_acceptance_validator_conforms_to_production_runner(self) -> None:
        module_path = (
            Path(__file__).parents[3]
            / "gidong-plc-mcp"
            / "src"
            / "xg5000_control"
            / "live"
            / "index_acceptance_bindings.py"
        )
        spec = importlib.util.spec_from_file_location(
            "_production_index_acceptance_bindings", module_path
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        signals = {
            role: f"signal_{index}"
            for index, role in enumerate(
                sorted(module.INPUT_ROLES | module.OUTPUT_ROLES)
            )
        }
        alarms = {
            role: f"alarm_{index}"
            for index, role in enumerate(sorted(module.ALARM_ROLES))
        }
        base_design = {
            "ioPoints": [
                {
                    "signalId": identity,
                    "kind": (
                        "digital_input"
                        if role in module.INPUT_ROLES
                        else "internal_bit"
                    ),
                }
                for role, identity in signals.items()
            ] + [
                {"signalId": f"latch_{index}", "kind": "internal_bit"}
                for index in range(3)
            ],
            "operatingModes": [
                {"modeId": value} for value in ("auto", "manual", "stopped")
            ],
            "sequences": [
                {"sequenceId": "auto_sequence", "modeId": "auto"},
                {"sequenceId": "manual_sequence", "modeId": "manual"},
            ],
            "alarms": [
                {"alarmId": identity, "latchSignalId": f"latch_{index}"}
                for index, identity in enumerate(alarms.values())
            ],
            "timers": [
                {"timerId": "timeout", "durationMs": 5000, "instruction": "TON"}
            ],
        }
        base_profile = {
            "schemaVersion": "controlstudio-index-acceptance-bindings/v1",
            "signals": signals,
            "modes": {"automatic": "auto", "manual": "manual"},
            "alarms": alarms,
            "timerId": "timeout",
        }

        def host_accepts(design: dict, profile: dict) -> bool:
            candidate = copy.deepcopy(design)
            candidate["indexAcceptanceBindings"] = profile
            return not _index_acceptance_binding_diagnostics(candidate)

        cases: list[tuple[dict, dict]] = [(base_design, base_profile)]
        for change in (
            "missing", "wrong_direction", "aliased", "uncovered_alarm",
            "unknown_mode", "wrong_timer",
        ):
            design = copy.deepcopy(base_design)
            profile = copy.deepcopy(base_profile)
            if change == "missing":
                del profile["signals"]["manual_start"]
            elif change == "wrong_direction":
                profile["signals"]["manual_start"] = profile["signals"]["motion"]
            elif change == "aliased":
                profile["signals"]["stop"] = profile["signals"]["reset"]
            elif change == "uncovered_alarm":
                design["alarms"].append(
                    {"alarmId": "extra", "latchSignalId": "latch_0"}
                )
            elif change == "unknown_mode":
                profile["modes"]["manual"] = "absent"
            else:
                design["timers"][0]["durationMs"] = 3000
            cases.append((design, profile))

        for design, profile in cases:
            try:
                module.IndexAcceptanceBindings.resolve(design, profile)
            except ValueError:
                runner_accepts = False
            else:
                runner_accepts = True
            self.assertEqual(host_accepts(design, profile), runner_accepts)

    def test_atomic_cross_section_repair_of_invalid_draft(self) -> None:
        changes = _design_changes()
        changes[5]["value"][0]["condition"]["source_id"] = "missing"
        saved = self.execute("design.edit", {"expected_revision": 0, "changes": changes})
        invalid = self.execute("design.check", {"expected_revision": saved["revision"]})
        self.assertEqual(invalid["status"], "invalid")
        before = self.service._repository.get("planning-conversation")
        batch = [
            {"operation": "patch_item", "section": "control_networks", "item_id": "run_network",
             "value": {"permit_all": [{"signal_id": "permit", "state": "off"}]}},
            {"operation": "patch_item", "section": "ladder_rungs", "item_id": "run_output_rung",
             "value": {"condition": {"kind": "contact", "source_type": "signal", "source_id": "permit", "state": "off"}}},
        ]
        # A bad second item must roll back the first item, not leave half a repair.
        rejected = self.execute("design.edit", {"expected_revision": before.revision,
            "changes": [batch[0], {**batch[1], "item_id": "does_not_exist"}]})
        self.assertEqual(rejected["status"], "not_applied")
        self.assertEqual(self.service._repository.get("planning-conversation").document, before.document)
        repaired = self.execute("design.edit", {"expected_revision": before.revision, "changes": batch})
        self.assertEqual(repaired["status"], "applied")
        self.assertEqual(repaired["revision"], before.revision + 1)
        after = self.service._repository.get("planning-conversation")
        self.assertEqual(after.document["native_design"]["io_points"], before.document["native_design"]["io_points"])
        checked = self.execute("design.check", {"expected_revision": repaired["revision"]})
        self.assertEqual(checked["status"], "valid", checked.get("diagnostics"))

    def test_empty_network_sync_does_not_create_empty_ladder_condition(self) -> None:
        changes = _design_changes()
        changes[4]["value"][0]["permit_all"] = []
        applied = self.execute("design.edit", {"expected_revision": 0, "changes": changes})
        with self.assertRaisesRegex(ProjectDesignDraftConflict, "design.edit_ladder_rungs"):
            self.execute("design.sync_control_network_out_rung", {
                "expected_revision": applied["revision"],
                "rung_id": "derived_rung", "network_id": "run_network",
            })
        draft = self.service._repository.get("planning-conversation")
        self.assertEqual(draft.revision, applied["revision"])

    def test_internal_or_rung_compiles_without_fabricated_common_permit(self) -> None:
        changes = _design_changes()
        changes[2]["value"].append({**changes[2]["value"][0], "signal_id": "second_input",
                                   "symbol": "SECOND_INPUT", "address": "M00010"})
        changes[4]["value"][0]["permit_all"] = []
        first = changes[5]["value"][0]["condition"]
        changes[5]["value"][0]["condition"] = {
            "kind": "any_of", "terms": [first, {**first, "source_id": "second_input"}]}
        applied = self.execute("design.edit", {"expected_revision": 0, "changes": changes})
        checked = self.execute("design.check", {"expected_revision": applied["revision"]})
        self.assertEqual(checked["status"], "valid", checked.get("diagnostics"))

        # A mistaken declaration must be repairable without rewriting the OR
        # program to match it.
        wrong = self.execute("design.edit_control_networks", {
            "expected_revision": checked["revision"], "changes": [{
                "operation": "patch_item", "item_id": "run_network",
                "value": {"permit_all": [{"signal_id": "permit", "state": "on"}]},
            }],
        })
        invalid = self.execute("design.check", {"expected_revision": wrong["revision"]})
        self.assertEqual(invalid["diagnostics"][0]["code"], "control_network_out_missing_effective_permits")
        self.assertIn("design.edit_control_networks", invalid["diagnostics"][0]["message"])
        self.assertIn("Preserve required safety-rule permits", invalid["diagnostics"][0]["message"])
        repaired = self.execute("design.edit_control_networks", {
            "expected_revision": invalid["revision"], "changes": [{
                "operation": "patch_item", "item_id": "run_network", "value": {"permit_all": []},
            }],
        })
        self.assertEqual(repaired["status"], "applied", repaired.get("diagnostics"))
        final = self.execute("design.check", {"expected_revision": repaired["revision"]})
        self.assertEqual(final["status"], "valid", final.get("diagnostics"))

    def test_digital_output_still_requires_explicit_permits(self) -> None:
        design = {change["section"]: change["value"] for change in _design_changes()[2:]}
        design["io_points"][1]["kind"] = "digital_output"
        design["control_networks"][0]["permit_all"] = []
        with self.assertRaisesRegex(ValueError, "digital_output control networks require"):
            ProjectPlanNativeDesignV1.model_validate(design)

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self._temporary.name) / "host.sqlite3"
        self.service = ProjectDesignDraftService(ProjectDesignDraftRepository(self.database_path))
        self.arguments = {
            "conversation_id": "planning-conversation",
            "run_id": "run-one",
            "brief_digest": "a" * 64,
            "project_brief": _brief(),
        }

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def execute(self, tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
        return asyncio.run(
            self.service.execute(
                tool_name=tool_name,
                arguments=arguments,
                **self.arguments,
            )
        )

    def test_summary_keeps_all_diagnostic_codes_beyond_four_details(self) -> None:
        seeded = self.execute(
            "design.edit", {"expected_revision": 0, "changes": _design_changes()}
        )
        draft = self.service._repository.get("planning-conversation")
        unknown = {
            "code": "unknown_reference",
            "path": "/ladderRungs/0/action/targetId",
            "message": "Reference is not declared.",
        }
        diagnostics = [copy.deepcopy(unknown) for _ in range(4)] + [
            {
                "code": "sequence_transition_rung_missing_or_incomplete",
                "path": "/sequences/0/steps/0/completeAll",
                "message": "Transition rung is missing.",
                "received": {
                    "sequenceId": "seq_auto", "sourceStepId": "step_wait",
                    "targetId": "step_move", "targetType": "step",
                },
            },
            {
                "code": "control_network_out_cardinality_mismatch",
                "path": "/controlNetworks",
                "message": "A network writer is missing.",
            },
        ]
        self.service._repository.update(
            conversation_id="planning-conversation",
            expected_revision=seeded["revision"],
            brief_digest=draft.brief_digest,
            document=draft.document,
            diagnostics=diagnostics,
            status="invalid",
            published_digest=None,
            run_id="run-one",
        )

        summary = self.execute("design.inspect", {})

        self.assertEqual(len(summary["diagnostics"]), 4)
        self.assertEqual(
            summary["diagnosticCodes"],
            [
                "control_network_out_cardinality_mismatch",
                "sequence_transition_rung_missing_or_incomplete",
                "unknown_reference",
            ],
        )
        self.assertEqual(summary["diagnosticCodeCounts"]["unknown_reference"], 4)
        self.assertEqual(
            {item["code"] for item in summary["diagnosticRepresentatives"]},
            set(summary["diagnosticCodes"]),
        )

    def test_invalid_reference_is_retained_and_repairable_without_regeneration(self) -> None:
        inspected = self.execute("design.inspect", {})
        self.assertEqual(inspected["revision"], 0)

        applied = self.execute(
            "design.edit",
            {"expected_revision": 0, "changes": _design_changes(target_id="missing")},
        )
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(applied["revision"], 1)

        invalid = self.execute("design.check", {"expected_revision": 1})
        self.assertEqual(invalid["status"], "invalid")
        self.assertEqual(invalid["revision"], 2)
        self.assertEqual(
            len(invalid["diagnostics"]),
            min(invalid["diagnosticCount"], 4),
        )
        self.assertGreaterEqual(invalid["diagnosticCount"], 1)
        self.assertEqual(
            invalid["remainingDiagnosticCount"],
            invalid["diagnosticCount"] - len(invalid["diagnostics"]),
        )
        diagnostic = invalid["diagnostics"][0]
        self.assertEqual(
            diagnostic["path"],
            "/ladderRungs/0/action/targetId",
        )
        self.assertEqual(diagnostic["received"], "missing")
        self.assertIn("run_output", diagnostic["relatedIds"])
        self.assertNotIn("ladder_rungs", invalid["completeSections"])

        repaired = self.execute(
            "design.edit_ladder_rungs",
            {
                "expected_revision": 2,
                "changes": [
                    {
                        "operation": "patch_item",
                        "item_id": "run_output_rung",
                        "value": _design_changes()[5]["value"][0],
                    }
                ],
            },
        )
        self.assertEqual(repaired["status"], "applied")
        self.assertEqual(repaired["revision"], 3)

        checked = self.execute("design.check", {"expected_revision": 3})
        self.assertEqual(checked["status"], "valid")
        published = self.execute(
            "design.publish_review",
            {"expected_revision": checked["revision"]},
        )
        self.assertEqual(published["status"], "published")
        self.assertEqual(published["draft"]["remaining_work"], [])

    def test_message_requirement_source_crosses_edit_check_and_publish_boundary(self) -> None:
        message_id = "same-conversation-requirement"
        quote = "완료 횟수는 여섯 번으로 바꾼다."
        self.arguments["project_brief"] = {
            **_brief(),
            "requirement_message_sources": [
                {"message_id": message_id, "content": quote}
            ],
        }
        changes = _design_changes()
        changes[0]["value"]["requirement_sources"] = [
            {"field": "sequence", "quote": quote, "message_id": message_id}
        ]
        applied = self.execute(
            "design.edit", {"expected_revision": 0, "changes": changes}
        )
        checked = self.execute(
            "design.check", {"expected_revision": applied["revision"]}
        )
        self.assertEqual(checked["status"], "valid")
        published = self.execute(
            "design.publish_review", {"expected_revision": checked["revision"]}
        )
        self.assertEqual(published["status"], "published")

        self.arguments["project_brief"] = _brief()
        published_revision = self.service._repository.get(
            "planning-conversation"
        ).revision
        rejected = self.execute(
            "design.check", {"expected_revision": published_revision}
        )
        self.assertEqual(rejected["status"], "invalid")
        self.assertEqual(
            rejected["diagnostics"][0]["path"],
            "/narrative/requirement_sources",
        )

    def test_missing_control_output_routes_to_io_declaration_repair(self) -> None:
        changes = _design_changes()
        changes[4]["value"][0]["output_signal_id"] = "missing_output"
        applied = self.execute(
            "design.edit",
            {"expected_revision": 0, "changes": changes},
        )
        invalid = self.execute(
            "design.check",
            {"expected_revision": applied["revision"]},
        )
        self.assertEqual(invalid["diagnostics"][0]["code"], "unknown_reference")
        self.assertEqual(
            invalid["diagnostics"][0]["path"],
            "/controlNetworks/0/outputSignalId",
        )
        self.assertEqual(invalid["repairContext"]["section"], "io_points")
        self.assertEqual(invalid["repairContext"]["items"], [])
        self.assertEqual(
            invalid["repairContext"]["missingDeclarations"][0]["id"],
            "missing_output",
        )
        self.assertEqual(
            invalid["repairContext"]["occupiedAddresses"],
            ["M00000", "M02000"],
        )

    def test_condition_only_patch_persists_without_rewriting_description(self) -> None:
        changes = _design_changes()
        original = changes[5]["value"][0]
        applied = self.execute("design.edit", {"expected_revision": 0, "changes": changes})
        replacement = {**original["condition"], "state": "off"}
        patched = self.execute("design.edit_ladder_rungs", {
            "expected_revision": applied["revision"], "changes": [{
                "operation": "patch_item", "item_id": original["rung_id"],
                "value": {"condition": replacement},
            }],
        })
        self.assertEqual(patched["status"], "applied", patched)
        self.assertTrue(patched["editImpact"]["executableChanged"])
        self.assertEqual(patched["editImpact"]["changedFieldsByRung"][original["rung_id"]], ["condition"])
        read = self.execute("design.read", {"sections": ["ladder_rungs"]})
        stored = read["sections"]["ladder_rungs"][0]
        self.assertEqual(stored["condition"], replacement)
        self.assertEqual(stored["description"], original["description"])
        self.assertEqual(stored["action"], original["action"])
        # The edit is durable even when its resulting behavior violates the
        # declared permit; check must diagnose that actual changed behavior.
        checked = self.execute("design.check", {"expected_revision": patched["revision"]})
        self.assertEqual(checked["status"], "invalid")

    def test_ladder_rung_can_move_before_an_existing_rung_without_rewrite(self) -> None:
        changes = _design_changes()
        first = changes[5]["value"][0]
        second = {
            **first,
            "rung_id": "second_rung",
            "description": "두 번째 래더",
            "condition": {
                "kind": "contact",
                "source_type": "signal",
                "source_id": "permit",
                "state": "off",
            },
        }
        changes[5]["value"] = [first, second]
        applied = self.execute(
            "design.edit",
            {"expected_revision": 0, "changes": changes},
        )
        self.assertEqual(applied.get("status"), "applied", applied)

        patched = self.execute(
            "design.patch_ladder_rung",
            {
                "expected_revision": applied["revision"],
                "rung_id": "second_rung",
                "patch": {"description": "수정된 두 번째 래더"},
            },
        )
        self.assertEqual(patched["status"], "applied")

        incremented = self.execute(
            "design.set_ladder_increment_action",
            {
                "expected_revision": patched["revision"],
                "rung_id": "second_rung",
                "destination_address": "D00100",
                "condition": second["condition"],
                "description": "수정된 두 번째 래더",
            },
        )
        self.assertEqual(incremented["status"], "applied")

        moved = self.execute(
            "design.move_ladder_rung",
            {
                "expected_revision": incremented["revision"],
                "rung_id": "second_rung",
                "before_rung_id": "run_output_rung",
            },
        )
        self.assertEqual(moved["status"], "applied")
        read = self.execute("design.read", {"sections": ["ladder_rungs"]})
        self.assertEqual(
            [item["rung_id"] for item in read["sections"]["ladder_rungs"]],
            ["second_rung", "run_output_rung"],
        )
        self.assertEqual(
            read["sections"]["ladder_rungs"][0]["action"],
            {"instruction": "INCP", "destination_address": "D00100"},
        )
        self.assertEqual(
            read["sections"]["ladder_rungs"][0]["description"],
            "수정된 두 번째 래더",
        )

    def test_increment_tool_creates_a_missing_counter_rung_atomically(self) -> None:
        applied = self.execute(
            "design.edit",
            {"expected_revision": 0, "changes": _design_changes()},
        )
        created = self.execute(
            "design.set_ladder_increment_action",
            {
                "expected_revision": applied["revision"],
                "rung_id": "completion_count_increment",
                "destination_address": "D00100",
                "condition": {
                    "kind": "contact",
                    "source_type": "signal",
                    "source_id": "permit",
                    "state": "on",
                },
                "description": "완료 이벤트 펄스 카운트",
            },
        )
        self.assertEqual(created["status"], "applied")
        read = self.execute("design.read", {"sections": ["ladder_rungs"]})
        new_rung = read["sections"]["ladder_rungs"][-1]
        self.assertEqual(new_rung["rung_id"], "completion_count_increment")
        self.assertEqual(
            new_rung["action"],
            {"instruction": "INCP", "destination_address": "D00100"},
        )

    def test_alarm_sync_tools_copy_declared_trigger_and_recovery_contracts(self) -> None:
        changes = _design_changes()
        changes[2]["value"].extend(
            [
                {
                    "signal_id": "alarm_cause",
                    "symbol": "ALARM_CAUSE",
                    "address": "M00002",
                    "kind": "digital_input",
                    "description": "알람 원인",
                    "normal_state": "off",
                    "safe_state": "not_applicable",
                },
                {
                    "signal_id": "reset_btn",
                    "symbol": "RESET_BTN",
                    "address": "M00003",
                    "kind": "digital_input",
                    "description": "복구 버튼",
                    "normal_state": "off",
                    "safe_state": "not_applicable",
                },
                {
                    "signal_id": "alarm_latch",
                    "symbol": "ALARM_LATCH",
                    "address": "M00004",
                    "kind": "internal_bit",
                    "description": "알람 래치",
                    "normal_state": "off",
                    "safe_state": "off",
                },
            ]
        )
        changes[8]["value"] = [
            {
                "alarm_id": "alarm_one",
                "name": "원인 알람",
                "latch_signal_id": "alarm_latch",
                "mode_id": None,
                "step_id": None,
                "trigger_all": [{"signal_id": "alarm_cause", "state": "on"}],
                "affected_output_signal_ids": ["run_output"],
                "description": "원인 알람 발생",
            }
        ]
        changes[9]["value"] = [
            {
                "recovery_id": "recover_one",
                "name": "원인 알람 복구",
                "when_all": [
                    {"signal_id": "alarm_cause", "state": "off"},
                    {"signal_id": "reset_btn", "state": "on"},
                ],
                "reset_alarm_ids": ["alarm_one"],
                "reset_sequence_ids": [],
                "description": "원인 해제 뒤 복구",
            }
        ]
        changes[5]["value"].extend(
            [
                {
                    "rung_id": "alarm_set",
                    "description": "잘못된 SET 조건",
                    "condition": {
                        "kind": "contact",
                        "source_type": "signal",
                        "source_id": "permit",
                        "state": "on",
                    },
                    "action": {
                        "instruction": "SET",
                        "target_type": "signal",
                        "target_id": "alarm_latch",
                    },
                },
                {
                    "rung_id": "alarm_reset",
                    "description": "잘못된 RST 조건",
                    "condition": {
                        "kind": "contact",
                        "source_type": "signal",
                        "source_id": "permit",
                        "state": "off",
                    },
                    "action": {
                        "instruction": "RST",
                        "target_type": "signal",
                        "target_id": "alarm_latch",
                    },
                },
            ]
        )
        applied = self.execute(
            "design.edit",
            {"expected_revision": 0, "changes": changes},
        )
        trigger = self.execute(
            "design.sync_alarm_trigger_rung",
            {
                "expected_revision": applied["revision"],
                "rung_id": "alarm_set",
                "alarm_id": "alarm_one",
            },
        )
        recovery = self.execute(
            "design.sync_alarm_recovery_rung",
            {
                "expected_revision": trigger["revision"],
                "rung_id": "alarm_reset",
                "recovery_id": "recover_one",
            },
        )
        self.assertEqual(trigger["status"], "applied")
        self.assertEqual(recovery["status"], "applied")
        rungs = self.execute("design.read", {"sections": ["ladder_rungs"]})[
            "sections"
        ]["ladder_rungs"]
        alarm_set = next(item for item in rungs if item["rung_id"] == "alarm_set")
        alarm_reset = next(item for item in rungs if item["rung_id"] == "alarm_reset")
        self.assertEqual(alarm_set["condition"]["source_id"], "alarm_cause")
        self.assertEqual(
            [term["source_id"] for term in alarm_reset["condition"]["terms"]],
            ["alarm_cause", "reset_btn"],
        )

    def test_sequence_sync_tools_copy_start_transition_and_reset_contracts(self) -> None:
        changes = _design_changes()
        changes[7]["value"] = [
            {
                "sequence_id": "sequence_one",
                "name": "단일 시퀀스",
                "mode_id": None,
                "restart_policy": "continuous",
                "start_all": [{"signal_id": "permit", "state": "on"}],
                "reset_all": [{"signal_id": "permit", "state": "off"}],
                "steps": [
                    {
                        "step_id": "step_one",
                        "name": "첫 단계",
                        "address": "S00.01",
                        "complete_all": [{"signal_id": "permit", "state": "on"}],
                        "description": "첫 단계 완료",
                    },
                    {
                        "step_id": "step_two",
                        "name": "둘째 단계",
                        "address": "S00.02",
                        "complete_all": [{"signal_id": "permit", "state": "off"}],
                        "description": "둘째 단계 완료",
                    },
                ],
            }
        ]
        applied = self.execute(
            "design.edit", {"expected_revision": 0, "changes": changes}
        )
        started = self.execute(
            "design.sync_sequence_start_rung",
            {
                "expected_revision": applied["revision"],
                "rung_id": "sequence_start",
                "sequence_id": "sequence_one",
            },
        )
        transitioned = self.execute(
            "design.sync_sequence_transition_rung",
            {
                "expected_revision": started["revision"],
                "rung_id": "sequence_transition",
                "sequence_id": "sequence_one",
                "source_step_id": "step_one",
            },
        )
        reset = self.execute(
            "design.sync_sequence_reset_rung",
            {
                "expected_revision": transitioned["revision"],
                "rung_id": "sequence_reset",
                "sequence_id": "sequence_one",
            },
        )
        self.assertEqual(reset["status"], "applied")
        rungs = self.execute("design.read", {"sections": ["ladder_rungs"]})[
            "sections"
        ]["ladder_rungs"]
        by_id = {item["rung_id"]: item for item in rungs}
        self.assertEqual(
            by_id["sequence_start"]["action"],
            {"instruction": "SET", "target_type": "step", "target_id": "step_one"},
        )
        self.assertEqual(
            by_id["sequence_transition"]["action"],
            {"instruction": "SET", "target_type": "step", "target_id": "step_two"},
        )
        self.assertEqual(
            by_id["sequence_reset"]["action"],
            {
                "instruction": "SET",
                "target_type": "sequence_reset",
                "target_id": "sequence_one",
            },
        )
        draft = self.service._repository.get("planning-conversation")
        invalid = self.service._repository.update(
            conversation_id=draft.conversation_id, expected_revision=draft.revision,
            brief_digest=draft.brief_digest, document=draft.document,
            diagnostics=[{"code": "sequence_reset_rung_missing_or_incomplete",
                          "path": "/sequences/0/resetAll", "message": "reset declaration mismatch"}],
            status="invalid", run_id="run-one", published_digest=None,
        )
        repaired = self.execute("design.edit_sequences", {
            "expected_revision": invalid.revision,
            "changes": [{"operation": "patch_item", "item_id": "sequence_one",
                         "value": {"reset_all": [{"signal_id": "permit", "state": "on"}]}}],
        })
        self.assertEqual(repaired["status"], "applied", repaired)

        for code, state in (
            ("sequence_start_rung_missing_or_incomplete", "off"),
            ("sequence_transition_rung_missing_or_incomplete", "on"),
        ):
            draft = self.service._repository.get("planning-conversation")
            invalid = self.service._repository.update(
                conversation_id=draft.conversation_id, expected_revision=draft.revision,
                brief_digest=draft.brief_digest, document=draft.document,
                diagnostics=[{"code": code, "path": "/sequences/0/startAll",
                              "message": "declaration mismatch"}],
                status="invalid", run_id="run-one", published_digest=None,
            )
            repaired = self.execute("design.edit_sequences", {
                "expected_revision": invalid.revision,
                "changes": [{"operation": "patch_item", "item_id": "sequence_one",
                             "value": {"start_all": [{"signal_id": "permit", "state": state}]}}],
            })
            self.assertEqual(repaired["status"], "applied", repaired)

    def test_counter_reset_and_control_out_specialized_tools(self) -> None:
        applied = self.execute(
            "design.edit", {"expected_revision": 0, "changes": _design_changes()}
        )
        counter_reset = self.execute(
            "design.set_ladder_counter_reset_action",
            {
                "expected_revision": applied["revision"],
                "rung_id": "counter_reset",
                "destination_address": "D00000",
                "condition": {
                    "kind": "contact",
                    "source_type": "signal",
                    "source_id": "permit",
                    "state": "off",
                },
                "description": "카운터 초기화",
            },
        )
        network_out = self.execute(
            "design.sync_control_network_out_rung",
            {
                "expected_revision": counter_reset["revision"],
                "rung_id": "run_output_rung",
                "network_id": "run_network",
            },
        )
        self.assertEqual(network_out["status"], "applied")
        rungs = self.execute("design.read", {"sections": ["ladder_rungs"]})[
            "sections"
        ]["ladder_rungs"]
        by_id = {item["rung_id"]: item for item in rungs}
        self.assertEqual(by_id["counter_reset"]["action"]["instruction"], "MOV")
        self.assertEqual(by_id["counter_reset"]["action"]["source_constant"], 0)
        self.assertEqual(by_id["run_output_rung"]["action"]["instruction"], "OUT")

    def test_missing_counter_reset_can_be_added_despite_increment_index_path(self) -> None:
        self.execute("design.edit", {"expected_revision": 0, "changes": _design_changes()})
        draft = self.service._repository.get("planning-conversation")
        assert draft is not None
        original_rungs = draft.document["native_design"]["ladder_rungs"]
        original_id = original_rungs[0]["rung_id"]
        draft = self.service._repository.update(
            conversation_id=draft.conversation_id,
            expected_revision=draft.revision,
            brief_digest=draft.brief_digest,
            document=draft.document,
            diagnostics=[{
                "code": "increment_reset_writer_mismatch",
                "path": "/ladderRungs/0/action/destinationAddress",
                "message": "missing reset writer",
                "received": {"incrementRungId": original_id,
                             "destinationAddress": "D00000", "resetRungIds": []},
                "relatedIds": [original_id],
            }],
            status="invalid", published_digest=None, run_id="run-one",
        )
        result = self.execute("design.set_ladder_counter_reset_action", {
            "expected_revision": draft.revision,
            "rung_id": "counter_reset", "destination_address": "D00000",
            "condition": {"kind": "contact", "source_type": "signal",
                          "source_id": "permit", "state": "off"},
            "description": "카운터 초기화",
        })
        self.assertEqual(result["status"], "applied", result)
        rungs = self.execute("design.read", {"sections": ["ladder_rungs"]})[
            "sections"]["ladder_rungs"]
        self.assertEqual(rungs[:-1], original_rungs)
        self.assertEqual(rungs[-1]["action"]["instruction"], "MOV")

    def test_missing_counter_diagnostic_does_not_lock_repair_to_textual_hint(self) -> None:
        brief = ProjectBriefV1.model_validate(
            {**_brief(), "performance_and_acceptance": "완료 카운트가 6까지 증가한다."}
        )
        design = {
            "ioPoints": [{"signalId": "permit", "kind": "digital_input"}],
            "operatingModes": [],
            "controlNetworks": [],
            "timers": [],
            "sequences": [],
            "alarms": [],
            "recoveryRules": [],
            "safetyRules": [],
            "ladderRungs": [
                {
                    "rungId": "move_timeout_timer",
                    "description": "이동 제한시간 5초 카운트",
                    "condition": {
                        "kind": "contact",
                        "sourceType": "signal",
                        "sourceId": "permit",
                        "state": "on",
                    },
                    "action": {
                        "instruction": "OUT",
                        "targetType": "signal",
                        "targetId": "permit",
                    },
                }
            ],
        }
        diagnostic = next(
            item
            for item in _requirement_diagnostics(design, brief)
            if item["code"] == "missing_completion_counter_increment"
        )
        self.assertEqual(diagnostic.get("relatedIds", []), [])
        self.assertEqual(
            diagnostic["received"]["possibleCounterRungIds"],
            ["move_timeout_timer"],
        )

    def test_mode_stopped_is_not_misclassified_as_e_stop_hazard(self) -> None:
        design = {
            "ioPoints": [
                {"signalId": "mode_stopped", "kind": "internal_bit"},
                {"signalId": "run_output", "kind": "internal_bit"},
            ],
            "operatingModes": [],
            "controlNetworks": [],
            "timers": [],
            "sequences": [],
            "alarms": [],
            "recoveryRules": [],
            "safetyRules": [],
            "ladderRungs": [
                {
                    "rungId": "mode_ready",
                    "condition": {
                        "kind": "contact",
                        "sourceType": "signal",
                        "sourceId": "mode_stopped",
                        "state": "on",
                    },
                    "action": {
                        "instruction": "OUT",
                        "targetType": "signal",
                        "targetId": "run_output",
                    },
                }
            ],
        }
        codes = {
            item["code"]
            for item in _requirement_diagnostics(
                design,
                ProjectBriefV1.model_validate(_brief()),
            )
        }
        self.assertNotIn("hazard_active_used_as_output_permit", codes)

    def test_field_repair_cannot_remove_an_unrelated_valid_item(self) -> None:
        changes = _design_changes(target_id="missing")
        valid_rung = {
            **changes[5]["value"][0],
            "rung_id": "retained_valid_rung",
            "action": {
                "instruction": "OUT",
                "target_type": "signal",
                "target_id": "run_output",
            },
        }
        changes[5]["value"].append(valid_rung)
        self.execute("design.inspect", {})
        applied = self.execute(
            "design.edit",
            {"expected_revision": 0, "changes": changes},
        )
        invalid = self.execute(
            "design.check",
            {"expected_revision": applied["revision"]},
        )
        self.assertEqual(invalid["diagnostics"][0]["path"], "/ladderRungs/0/action/targetId")

        rejected = self.execute(
            "design.edit_ladder_rungs",
            {
                "expected_revision": invalid["revision"],
                "changes": [
                    {
                        "operation": "remove_item",
                        "item_id": "retained_valid_rung",
                    }
                ],
            },
        )

        self.assertEqual(rejected["status"], "not_applied")
        self.assertEqual(
            rejected["diagnostics"][0]["code"],
            "repair_must_target_diagnosed_item",
        )
        current = self.execute("design.read", {"sections": ["ladder_rungs"]})
        self.assertEqual(
            [item["rung_id"] for item in current["sections"]["ladder_rungs"]],
            ["run_output_rung", "retained_valid_rung"],
        )

    def test_diagnostic_allows_existing_dependency_patch_before_error_is_resolved(self) -> None:
        self.execute("design.inspect", {})
        applied = self.execute("design.edit", {
            "expected_revision": 0, "changes": _design_changes(target_id="missing"),
        })
        invalid = self.execute("design.check", {"expected_revision": applied["revision"]})
        patched = self.execute("design.edit_control_networks", {
            "expected_revision": invalid["revision"],
            "changes": [{"operation": "patch_item", "item_id": "run_network",
                         "value": {"permit_all": []}}],
        })
        self.assertEqual(patched["status"], "applied", patched.get("diagnostics"))
        current = self.execute("design.read", {"sections": ["control_networks"]})
        self.assertEqual(current["sections"]["control_networks"][0]["permit_all"],
                         [])
        checked = self.execute("design.check", {"expected_revision": patched["revision"]})
        self.assertEqual(checked["status"], "invalid")

    def test_diagnostic_repair_cannot_edit_a_different_section(self) -> None:
        self.execute("design.inspect", {})
        applied = self.execute(
            "design.edit",
            {"expected_revision": 0, "changes": _design_changes(target_id="missing")},
        )
        invalid = self.execute("design.check", {"expected_revision": applied["revision"]})
        retained_revision = invalid["revision"]

        rejected = self.execute(
            "design.edit_alarms",
            {
                "expected_revision": retained_revision,
                "changes": [
                    {
                        "operation": "create_item",
                        "item_id": "unrelated_alarm",
                        "value": {
                            "alarm_id": "unrelated_alarm",
                            "name": "Unrelated",
                            "description": "must not be added",
                            "trigger_all": [],
                            "latch_signal_id": "run_output",
                            "affected_output_signal_ids": [],
                        },
                    }
                ],
            },
        )

        self.assertEqual(rejected["status"], "not_applied")
        self.assertEqual(rejected["currentRevision"], retained_revision)
        self.assertEqual(rejected["diagnostics"][1]["code"], "repair_section_mismatch")
        read = self.execute("design.read", {"sections": ["alarms"]})
        self.assertEqual(read["revision"], retained_revision)
        self.assertEqual(read["sections"]["alarms"], [])

    def test_repair_context_routes_cross_section_diagnostics_to_the_owner(self) -> None:
        draft = self.service._repository.ensure(
            conversation_id="planning-conversation",
            brief_digest="a" * 64,
            project_name="모의 출력",
            run_id="run-one",
        )
        for code, path, expected_section in (
            (
                "alarm_output_missing_latch_off_interlock",
                "/alarms/0/affectedOutputSignalIds/0",
                "safety_rules",
            ),
            (
                "control_network_out_missing_effective_permits",
                "/controlNetworks/0/permitAll",
                "ladder_rungs",
            ),
            (
                "recovery_sequence_reset_rung_missing_or_incomplete",
                "/recoveryRules/0/whenAll",
                "ladder_rungs",
            ),
        ):
            draft = self.service._repository.update(
                conversation_id=draft.conversation_id,
                expected_revision=draft.revision,
                brief_digest=draft.brief_digest,
                document=draft.document,
                diagnostics=[{"code": code, "path": path, "message": code}],
                status="invalid",
                published_digest=None,
                run_id="run-one",
            )
            summary = self.service._summary(draft)
            self.assertEqual(summary["repairContext"]["section"], expected_section)
            if code == "alarm_output_missing_latch_off_interlock":
                self.assertEqual(summary["repairContext"]["items"], [])
                self.assertIn(
                    "Add the new safety-rule item", summary["repairContext"]["instruction"]
                )

    def test_structured_compiler_error_targets_and_allows_removing_unbound_rung(self) -> None:
        applied = self.execute("design.edit", {"expected_revision": 0, "changes": _design_changes()})
        with patch("controlstudio_host.planning_drafts._compile_native_program", side_effect=NativeProjectPreconditionError(
            "SET sequence rung is unbound", diagnostic_code="unbound_sequence_rung",
            repair_section="ladder_rungs", repair_item_ids=("run_output_rung",),
        )):
            checked = self.execute("design.check", {"expected_revision": applied["revision"]})
        self.assertEqual(checked["diagnostics"][0]["code"], "unbound_sequence_rung", checked)
        self.assertEqual(checked["repairContext"]["section"], "ladder_rungs")
        result = self.execute("design.edit_ladder_rungs", {
            "expected_revision": checked["revision"], "changes": [
                {"operation": "remove_item", "item_id": "run_output_rung"},
            ],
        })
        self.assertEqual(result["status"], "applied", result)

    def test_missing_recovery_rung_can_be_added_without_overwriting_normal_reset(self) -> None:
        self.execute("design.edit", {"expected_revision": 0, "changes": _design_changes()})
        draft = self.service._repository.get("planning-conversation")
        assert draft is not None
        original = draft.document["native_design"]["ladder_rungs"]
        draft = self.service._repository.update(
            conversation_id=draft.conversation_id, expected_revision=draft.revision,
            brief_digest=draft.brief_digest, document=draft.document,
            diagnostics=[{
                "code": "recovery_sequence_reset_rung_missing_or_incomplete",
                "path": "/recoveryRules/0/whenAll", "message": "add recovery-specific rung",
                "relatedIds": ["recovery", "sequence", original[0]["rung_id"]],
            }], status="invalid", published_digest=None, run_id="run-one",
        )
        self.assertEqual(self.service._summary(draft)["repairContext"]["section"], "ladder_rungs")
        result = self.execute("design.edit_ladder_rungs", {
            "expected_revision": draft.revision, "changes": [{
                "operation": "create_item", "item_id": "recovery_reset",
                "value": {
                    "rung_id": "recovery_reset", "description": "복구 전용 단계 초기화",
                    "condition": {"kind": "contact", "source_type": "signal", "source_id": "permit", "state": "off"},
                    "action": {"instruction": "SET", "target_type": "sequence_reset", "target_id": "sequence"},
                },
            }],
        })
        self.assertEqual(result["status"], "applied", result)
        rungs = self.execute("design.read", {"sections": ["ladder_rungs"]})["sections"]["ladder_rungs"]
        self.assertEqual(rungs[:-1], original)
        self.assertEqual(rungs[-1]["rung_id"], "recovery_reset")

    def test_missing_alarm_interlock_materializes_authoritative_safety_fields(self) -> None:
        self.execute("design.inspect", {})
        self.execute(
            "design.edit",
            {"expected_revision": 0, "changes": _design_changes()},
        )
        draft = self.service._repository.get("planning-conversation")
        assert draft is not None
        draft = self.service._repository.update(
            conversation_id=draft.conversation_id,
            expected_revision=draft.revision,
            brief_digest=draft.brief_digest,
            document=draft.document,
            diagnostics=[
                {
                    "code": "alarm_output_missing_latch_off_interlock",
                    "path": "/alarms/0/affectedOutputSignalIds/0",
                    "message": "missing independent alarm interlock",
                    "received": {
                        "alarmId": "alarm_test",
                        "latchSignalId": "permit",
                        "outputSignalId": "run_output",
                        "suggestedRuleId": "safety_alarm_test_run_output",
                    },
                    "relatedIds": ["safety_alarm_test_run_output"],
                }
            ],
            status="invalid",
            published_digest=None,
            run_id="run-one",
        )

        repaired = self.execute(
            "design.edit_safety_rules",
            {
                "expected_revision": draft.revision,
                "changes": [
                    {
                        "operation": "create_item",
                        "item_id": "safety_alarm_test_run_output",
                        "value": {
                            "rule_id": "safety_alarm_test_run_output",
                            "name": "Alarm test interlock",
                        },
                    }
                ],
            },
        )
        self.assertEqual(repaired["status"], "applied")
        read = self.execute("design.read", {"sections": ["safety_rules"]})
        self.assertEqual(
            read["sections"]["safety_rules"],
            [
                {
                    "rule_id": "safety_alarm_test_run_output",
                    "name": "Alarm test interlock",
                    "classification": "process_interlock",
                    "permit_when": {"signal_id": "permit", "state": "off"},
                    "affected_output_signal_ids": ["run_output"],
                    "action": "deenergize",
                }
            ],
        )

    def test_checked_diagnostic_cannot_be_bypassed_by_unrestricted_edit(self) -> None:
        self.execute("design.inspect", {})
        applied = self.execute(
            "design.edit",
            {"expected_revision": 0, "changes": _design_changes(target_id="missing")},
        )
        invalid = self.execute("design.check", {"expected_revision": applied["revision"]})

        rejected = self.execute(
            "design.edit",
            {
                "expected_revision": invalid["revision"],
                "changes": [
                    {
                        "operation": "replace_section",
                        "section": "alarms",
                        "value": [],
                    }
                ],
            },
        )

        self.assertEqual(rejected["status"], "not_applied")
        self.assertEqual(rejected["currentRevision"], invalid["revision"])
        self.assertEqual(
            rejected["diagnostics"][1]["code"],
            "repair_requires_scoped_edit_tool",
        )

    def test_invalid_draft_accepts_sourced_narrative_and_related_item_repair(self) -> None:
        self.execute("design.inspect", {})
        applied = self.execute(
            "design.edit",
            {"expected_revision": 0, "changes": _design_changes(target_id="missing")},
        )
        invalid = self.execute("design.check", {"expected_revision": applied["revision"]})
        updated_narrative = _narrative()
        updated_narrative["summary"] = "요구 출처와 관련 래더를 함께 보완한 설계"

        repaired = self.fake_model_execute(
            "design.edit",
            {
                "expected_revision": invalid["revision"],
                "changes": [
                    {
                        "operation": "replace_section",
                        "section": "narrative",
                        "value": updated_narrative,
                    },
                    {
                        "operation": "patch_item",
                        "section": "ladder_rungs",
                        "item_id": "run_output_rung",
                        "value": {
                            "action": {
                                "instruction": "OUT",
                                "target_type": "signal",
                                "target_id": "run_output",
                            }
                        },
                    },
                ],
            },
        )

        self.assertEqual(repaired["status"], "applied")
        self.assertEqual(repaired["diagnostics"], [])
        read = self.execute(
            "design.read", {"sections": ["narrative", "ladder_rungs"]}
        )
        self.assertEqual(read["status"], "working")
        self.assertEqual(
            read["sections"]["narrative"]["summary"],
            "요구 출처와 관련 래더를 함께 보완한 설계",
        )
        self.assertEqual(
            read["sections"]["ladder_rungs"][0]["action"]["target_id"],
            "run_output",
        )

    def test_binding_only_edit_starts_an_unvalidated_revision_and_noop_retains_check(self) -> None:
        self.execute("design.inspect", {})
        applied = self.execute("design.edit", {
            "expected_revision": 0, "changes": _design_changes(target_id="missing"),
        })
        invalid = self.execute("design.check", {"expected_revision": applied["revision"]})
        before = self.execute("design.read", {"sections": ["ladder_rungs"]})
        binding_change = {
            "operation": "replace_section", "section": "index_acceptance_bindings",
            "value": {"signals": {}, "modes": {}, "alarms": {}, "timer_id": "watch"},
        }
        # Combining it with executable replacement must not bypass the guard.
        rejected = self.execute("design.edit", {
            "expected_revision": invalid["revision"],
            "changes": [binding_change, {"operation": "replace_section", "section": "alarms", "value": []}],
        })
        self.assertEqual(rejected["status"], "not_applied")
        saved = self.execute("design.edit", {
            "expected_revision": invalid["revision"], "changes": [binding_change],
        })
        self.assertEqual(saved["status"], "applied")
        self.assertEqual(saved["diagnostics"], [])
        after = self.execute("design.read", {"sections": ["ladder_rungs", "index_acceptance_bindings"]})
        self.assertEqual(after["status"], "working")
        self.assertEqual(before["sections"]["ladder_rungs"], after["sections"]["ladder_rungs"])
        self.assertEqual(after["sections"]["index_acceptance_bindings"]["timer_id"], "watch")

        rechecked = self.execute("design.check", {"expected_revision": saved["revision"]})
        self.assertEqual(rechecked["status"], "invalid")
        unchanged = self.execute("design.edit", {
            "expected_revision": rechecked["revision"], "changes": [binding_change],
        })
        self.assertEqual(unchanged["status"], "not_applied")
        self.assertEqual(unchanged["revision"], rechecked["revision"])
        self.assertEqual(unchanged["retainedRevision"], rechecked["revision"])
        self.assertEqual(unchanged["diagnostics"][0]["code"], "edit_made_no_change")
        self.assertEqual(unchanged["diagnostics"][1], rechecked["diagnostics"][0])
        self.assertEqual(unchanged["diagnosticsFreshness"], "retained_not_rechecked")
        self.assertEqual(unchanged["nextAction"], "design.check")
        self.assertIn("not recomputed", unchanged["diagnostics"][0]["message"])

    def test_identical_repair_is_rejected_without_advancing_revision(self) -> None:
        self.execute("design.inspect", {})
        changes = _design_changes(target_id="missing")
        self.execute(
            "design.edit",
            {"expected_revision": 0, "changes": changes},
        )
        invalid = self.execute("design.check", {"expected_revision": 1})
        self.assertEqual(invalid["revision"], 2)

        unchanged = self.execute(
            "design.edit_ladder_rungs",
            {
                "expected_revision": 2,
                "changes": [
                    {
                        "operation": "patch_item",
                        "item_id": "run_output_rung",
                        "value": changes[5]["value"][0],
                    }
                ],
            },
        )

        self.assertEqual(unchanged["status"], "not_applied")
        self.assertEqual(unchanged["revision"], 2)
        self.assertEqual(unchanged["diagnostics"][0]["code"], "edit_made_no_change")
        self.assertEqual(unchanged["diagnostics"][1]["code"], "unknown_reference")

    def test_identical_patch_after_valid_check_does_not_count_check_metadata_as_edit(self) -> None:
        applied = self.execute("design.edit", {"expected_revision": 0, "changes": _design_changes()})
        checked = self.execute("design.check", {"expected_revision": applied["revision"]})
        self.assertEqual(checked["status"], "valid")
        rung = self.execute("design.read", {"sections": ["ladder_rungs"]})["sections"]["ladder_rungs"][0]
        unchanged = self.execute("design.edit_ladder_rungs", {
            "expected_revision": checked["revision"],
            "changes": [{"operation": "patch_item", "item_id": rung["rung_id"], "value": rung}],
        })
        self.assertEqual(unchanged["status"], "not_applied")
        self.assertEqual(unchanged["revision"], checked["revision"])
        self.assertEqual(unchanged["diagnostics"][0]["code"], "edit_made_no_change")

    def test_existing_collection_item_accepts_a_lossless_partial_patch(self) -> None:
        self.execute("design.inspect", {})
        applied = self.execute(
            "design.edit",
            {"expected_revision": 0, "changes": _design_changes()},
        )
        self.assertEqual(applied["status"], "applied")

        patched = self.execute(
            "design.edit_io_points",
            {
                "expected_revision": 1,
                "changes": [
                    {
                        "operation": "patch_item",
                        "item_id": "run_output",
                        "value": {"description": "수정된 운전 출력 설명"},
                    }
                ],
            },
        )

        self.assertEqual(patched["status"], "applied")
        read = self.execute(
            "design.read",
            {"sections": ["io_points"]},
        )
        output = next(
            item for item in read["sections"]["io_points"] if item["signal_id"] == "run_output"
        )
        self.assertEqual(output["description"], "수정된 운전 출력 설명")
        self.assertEqual(output["address"], "M00001")
        self.assertEqual(output["safe_state"], "off")

    def test_xgk_timer_address_is_padded_for_native_compiler(self) -> None:
        normalized = self.service._normalize_collection_value(
            "timers",
            "move_timeout",
            {
                "timer_id": "move_timeout",
                "address": "T500",
                "time_base_ms": 100,
                "preset_ticks": 50,
                "duration_ms": 5000,
            },
            plc_series="XGK",
        )

        self.assertEqual(normalized["address"], "T0500")

    def test_catalog_exposes_narrow_collection_edit_tools(self) -> None:
        catalog = {
            item.tool_name: item.parameters_json_schema for item in self.service.tool_catalog()
        }
        self.assertIn("design.edit_ladder_rungs", catalog)
        self.assertIn("design.edit_io_points", catalog)
        self.assertIn("design.edit_io_safe_states", catalog)
        legacy_variants = catalog["design.edit"]["properties"]["changes"]["items"]["oneOf"]
        self.assertEqual(
            {item["properties"]["section"]["enum"][0] for item in legacy_variants},
            {"narrative", "technical_configuration", "index_acceptance_bindings",
             "io_points", "operating_modes", "control_networks", "ladder_rungs",
             "timers", "sequences", "alarms", "recovery_rules", "safety_rules"},
        )
        self.assertIn("$defs", catalog["design.edit"])
        self.assertTrue(any(item["properties"]["operation"]["enum"] == ["patch_item"]
                            and item["properties"]["section"]["enum"] == ["ladder_rungs"]
                            for item in legacy_variants))
        rung_variants = catalog["design.edit_ladder_rungs"]["properties"]["changes"]["items"][
            "oneOf"
        ]
        self.assertTrue(all("section" not in item["properties"] for item in rung_variants))
        alarm_variants = catalog["design.edit_alarms"]["properties"]["changes"]["items"][
            "oneOf"
        ]
        alarm_by_operation = {
            item["properties"]["operation"]["enum"][0]: item for item in alarm_variants
        }
        self.assertEqual(
            set(alarm_by_operation),
            {"create_item", "patch_item", "remove_item"},
        )
        self.assertIn("$ref", alarm_by_operation["create_item"]["properties"]["value"])
        alarm_patch = alarm_by_operation["patch_item"]["properties"]["value"]
        self.assertNotIn("$ref", alarm_patch)
        self.assertNotIn("required", alarm_patch)
        self.assertIn("latch_signal_id", alarm_patch["properties"])

    def test_io_safe_state_patch_updates_only_named_fields(self) -> None:
        self.execute("design.inspect", {})
        applied = self.execute(
            "design.edit",
            {"expected_revision": 0, "changes": _design_changes()},
        )
        patched = self.execute(
            "design.edit_io_safe_states",
            {
                "expected_revision": applied["revision"],
                "changes": [
                    {"signal_id": "permit", "safe_state": "not_applicable"},
                    {"signal_id": "run_output", "safe_state": "on"},
                ],
            },
        )
        self.assertEqual(patched["status"], "applied")
        read = self.execute("design.read", {"sections": ["io_points"]})
        values = {item["signal_id"]: item for item in read["sections"]["io_points"]}
        self.assertEqual(values["permit"]["safe_state"], "not_applicable")
        self.assertEqual(values["run_output"]["safe_state"], "on")
        self.assertEqual(values["run_output"]["symbol"], "RUN_OUTPUT")

    def test_collection_edit_retains_valid_items_when_one_item_is_invalid(self) -> None:
        self.execute("design.inspect", {})
        valid = _design_changes()[2]["value"][0]
        invalid = {
            **_design_changes()[2]["value"][1],
            "signal_id": "bad_output",
            "symbol": "BAD_OUTPUT",
            "address": "INVALID",
        }

        partial = self.execute(
            "design.edit_io_points",
            {
                "expected_revision": 0,
                "changes": [
                    {"operation": "upsert_item", "item_id": valid["signal_id"], "value": valid},
                    {"operation": "upsert_item", "item_id": "bad_output", "value": invalid},
                ],
            },
        )

        self.assertEqual(partial["status"], "partially_applied")
        self.assertEqual(partial["revision"], 1)
        self.assertEqual(partial["collectionCounts"]["io_points"], 1)
        self.assertEqual(partial["appliedChanges"], 1)
        self.assertEqual(partial["rejectedChanges"], 1)
        self.assertTrue(partial["diagnostics"][0]["path"].startswith("/changes/1"))

        repaired = self.execute(
            "design.edit_io_points",
            {
                "expected_revision": 1,
                "changes": [
                    {
                        "operation": "create_item",
                        "item_id": "bad_output",
                        "value": {**invalid, "address": "M00200"},
                    }
                ],
            },
        )
        self.assertEqual(repaired["status"], "applied")
        self.assertEqual(repaired["collectionCounts"]["io_points"], 2)

    def test_collection_edit_normalizes_mechanical_io_fields(self) -> None:
        self.execute("design.inspect", {})
        applied = self.execute(
            "design.edit_io_points",
            {
                "expected_revision": 0,
                "changes": [
                    {
                        "operation": "create_item",
                        "item_id": "ready_input",
                        "value": {
                            "signal_id": "model_chose_another_name",
                            "address": "X000",
                            "kind": "digital_input",
                            "normal_state": "off",
                            "safe_state": "off",
                        },
                    },
                    {
                        "operation": "create_item",
                        "item_id": "move_output",
                        "value": {
                            "signal_id": "move_output",
                            "address": "P1009",
                            "kind": "digital_output",
                            "normal_state": "off",
                            "safe_state": "off",
                        },
                    },
                ],
            },
        )

        self.assertEqual(applied["status"], "applied")
        read = self.execute("design.read", {"sections": ["io_points"]})
        points = {item["signal_id"]: item for item in read["sections"]["io_points"]}
        point = points["ready_input"]
        self.assertEqual(point["signal_id"], "ready_input")
        self.assertEqual(point["symbol"], "READY_INPUT")
        self.assertEqual(point["description"], "ready_input")
        self.assertEqual(point["address"], "M00000")
        self.assertEqual(point["safe_state"], "not_applicable")
        self.assertEqual(points["move_output"]["address"], "M02009")

    def test_all_invalid_collection_edit_persists_diagnostics(self) -> None:
        self.execute("design.inspect", {})
        rejected = self.execute(
            "design.edit_io_points",
            {
                "expected_revision": 0,
                "changes": [
                    {
                        "operation": "upsert_item",
                        "item_id": "bad_input",
                        "value": {
                            "signal_id": "bad_input",
                            "address": "INVALID",
                            "kind": "digital_input",
                            "normal_state": "off",
                        },
                    }
                ],
            },
        )

        self.assertEqual(rejected["status"], "not_applied")
        self.assertEqual(rejected["revision"], 1)
        self.assertEqual(rejected["collectionCounts"]["io_points"], 0)
        self.assertTrue(rejected["diagnostics"])
        inspected = self.execute("design.inspect", {})
        self.assertEqual(inspected["revision"], 1)
        self.assertEqual(inspected["diagnostics"], rejected["diagnostics"])

    def test_internal_simulation_check_normalizes_physical_p_address(self) -> None:
        self.execute("design.inspect", {})
        changes = _design_changes()
        changes[2]["value"][0]["address"] = "P1000"
        applied = self.execute(
            "design.edit",
            {"expected_revision": 0, "changes": changes},
        )
        checked = self.execute(
            "design.check",
            {"expected_revision": applied["revision"]},
        )

        self.assertEqual(checked["status"], "valid")
        read = self.execute("design.read", {"sections": ["io_points"]})
        self.assertEqual(read["sections"]["io_points"][0]["address"], "M00000")
        self.assertEqual(read["sections"]["io_points"][1]["address"], "M02000")

    def test_xgsim_internal_m_brief_uses_byte_isolated_output_allocation(self) -> None:
        self.arguments["project_brief"] = {
            **_brief(),
            "machine_purpose": (
                "XGK-CPUE와 XG-SIM의 내부 M 신호만 사용해 PC에서 시험한다."
            ),
            "hardware_and_io": "",
        }
        self.execute("design.inspect", {})
        changes = _design_changes()
        changes[2]["value"][0]["address"] = "M0000"
        changes[2]["value"][1]["address"] = "M0001"
        applied = self.execute(
            "design.edit", {"expected_revision": 0, "changes": changes}
        )

        checked = self.execute(
            "design.check", {"expected_revision": applied["revision"]}
        )

        self.assertEqual(checked["status"], "valid")
        read = self.execute("design.read", {"sections": ["io_points"]})
        points = {item["signal_id"]: item for item in read["sections"]["io_points"]}
        self.assertEqual(points["permit"]["address"], "M0000")
        self.assertEqual(points["run_output"]["address"], "M02000")

    def test_host_and_agent_share_io_safe_state_rules(self) -> None:
        common = {
            "signal_id": "signal",
            "symbol": "SIGNAL",
            "address": "M00000",
            "description": "test signal",
            "normal_state": "off",
        }
        with self.assertRaisesRegex(ValueError, "digital_input safe_state"):
            ProjectPlanIoPointV1.model_validate(
                {**common, "kind": "digital_input", "safe_state": "off"}
            )
        with self.assertRaisesRegex(ValueError, "writable signal safe_state"):
            ProjectPlanIoPointV1.model_validate(
                {**common, "kind": "internal_bit", "safe_state": "not_applicable"}
            )

    def test_revision_conflict_preserves_current_draft_across_service_restart(self) -> None:
        self.execute("design.inspect", {})
        applied = self.execute(
            "design.edit",
            {"expected_revision": 0, "changes": _design_changes()},
        )
        self.assertEqual(applied["revision"], 1)

        conflict = self.execute(
            "design.edit",
            {"expected_revision": 0, "changes": _design_changes()},
        )
        self.assertEqual(conflict["status"], "revision_conflict")
        self.assertEqual(conflict["currentRevision"], 1)

        restarted = ProjectDesignDraftService(ProjectDesignDraftRepository(self.database_path))
        resumed = asyncio.run(
            restarted.execute(
                tool_name="design.read",
                arguments={"sections": ["ladder_rungs"]},
                **{**self.arguments, "run_id": "run-two"},
            )
        )
        self.assertEqual(resumed["revision"], 1)
        self.assertEqual(
            resumed["sections"]["ladder_rungs"][0]["rung_id"],
            "run_output_rung",
        )

    def test_alarm_interlock_diagnostic_names_exact_latch_and_output(self) -> None:
        design = {
            "ioPoints": [
                {"signalId": "fault", "kind": "internal_bit"},
                {"signalId": "motor", "kind": "digital_output"},
            ],
            "operatingModes": [],
            "controlNetworks": [],
            "ladderRungs": [],
            "timers": [],
            "sequences": [],
            "alarms": [
                {
                    "alarmId": "move_timeout",
                    "latchSignalId": "fault",
                    "affectedOutputSignalIds": ["motor"],
                    "triggerAll": [],
                }
            ],
            "recoveryRules": [],
            "safetyRules": [
                {
                    "ruleId": "wrong_fault_permit",
                    "classification": "process_interlock",
                    "permitWhen": {"signalId": "fault", "state": "on"},
                    "affectedOutputSignalIds": ["motor"],
                }
            ],
        }

        diagnostic = next(
            item
            for item in _reference_diagnostics(design)
            if item["code"] == "alarm_output_missing_latch_off_interlock"
        )
        self.assertEqual(diagnostic["path"], "/alarms/0/affectedOutputSignalIds/0")
        self.assertEqual(diagnostic["received"]["latchSignalId"], "fault")
        self.assertEqual(diagnostic["received"]["outputSignalId"], "motor")
        self.assertEqual(diagnostic["relatedIds"], ["safety_move_timeout_motor"])
        self.assertEqual(
            diagnostic["received"]["suggestedRuleId"],
            "safety_move_timeout_motor",
        )
        self.assertIn("preserve every existing rule", diagnostic["message"])

    def test_stop_diagnostic_batches_every_unguarded_output_rung(self) -> None:
        design = {
            "ioPoints": [
                {
                    "signalId": "stop_btn",
                    "symbol": "STOP_BTN",
                    "kind": "digital_input",
                    "description": "정지 버튼",
                },
                {
                    "signalId": "move_command",
                    "symbol": "MOVE_COMMAND",
                    "kind": "digital_output",
                    "description": "이동 명령",
                },
                {
                    "signalId": "system_running",
                    "symbol": "SYSTEM_RUNNING",
                    "kind": "internal_bit",
                    "description": "시스템 동작 중",
                },
            ],
            "operatingModes": [],
            "controlNetworks": [],
            "ladderRungs": [
                {
                    "rungId": "move_out",
                    "condition": {
                        "kind": "contact",
                        "sourceType": "signal",
                        "sourceId": "system_running",
                        "state": "on",
                    },
                    "action": {
                        "instruction": "OUT",
                        "targetType": "signal",
                        "targetId": "move_command",
                    },
                },
                {
                    "rungId": "run_out",
                    "condition": {
                        "kind": "contact",
                        "sourceType": "signal",
                        "sourceId": "move_command",
                        "state": "on",
                    },
                    "action": {
                        "instruction": "OUT",
                        "targetType": "signal",
                        "targetId": "system_running",
                    },
                },
            ],
            "timers": [],
            "sequences": [],
            "alarms": [],
            "recoveryRules": [],
            "safetyRules": [],
        }

        diagnostic = next(
            item
            for item in _requirement_diagnostics(design, ProjectBriefV1.model_validate(_brief()))
            if item["code"] == "stop_interlock_missing_from_output_paths"
        )

        self.assertEqual(diagnostic["path"], "/ladderRungs")
        self.assertEqual(
            [item["rungId"] for item in diagnostic["received"]["candidateRungs"]],
            ["move_out", "run_out"],
        )
        self.assertEqual(diagnostic["relatedIds"], ["move_out", "run_out", "stop_btn"])

    def test_manual_release_diagnostic_rejects_same_step_completion_and_release(self) -> None:
        brief = ProjectBriefV1.model_validate(
            {
                **_brief(),
                "operating_modes": "수동은 버튼 한 번에 한 칸 이동한다.",
                "sequence": "수동 시작 버튼은 놓아야 다음 입력을 받는다.",
            }
        )
        design = {
            "ioPoints": [
                {
                    "signalId": "start_btn",
                    "symbol": "START_BTN",
                    "kind": "digital_input",
                    "description": "시작 버튼",
                },
                {
                    "signalId": "move_complete",
                    "symbol": "MOVE_COMPLETE",
                    "kind": "digital_input",
                    "description": "이동 완료",
                },
                {
                    "signalId": "mode_manual",
                    "symbol": "MODE_MANUAL",
                    "kind": "internal_bit",
                    "description": "수동 모드",
                },
            ],
            "operatingModes": [
                {
                    "modeId": "manual_mode",
                    "name": "수동",
                    "activation": {"signalId": "mode_manual", "state": "on"},
                }
            ],
            "controlNetworks": [],
            "ladderRungs": [],
            "timers": [],
            "sequences": [
                {
                    "sequenceId": "manual_sequence",
                    "modeId": "manual_mode",
                    "restartPolicy": "require_start_release",
                    "startAll": [{"signalId": "start_btn", "state": "on"}],
                    "resetAll": [
                        {"signalId": "move_complete", "state": "on"},
                        {"signalId": "start_btn", "state": "off"},
                    ],
                    "steps": [
                        {
                            "stepId": "manual_move",
                            "completeAll": [
                                {"signalId": "move_complete", "state": "on"},
                                {"signalId": "start_btn", "state": "off"},
                            ],
                        }
                    ],
                }
            ],
            "alarms": [],
            "recoveryRules": [],
            "safetyRules": [],
        }

        codes = {item["code"] for item in _requirement_diagnostics(design, brief)}
        self.assertIn("manual_completion_must_not_wait_for_button_release", codes)
        self.assertIn("manual_sequence_missing_idle_initial_arm", codes)

    def test_continuous_auto_requires_run_latch_and_no_raw_start_cycle_gate(self) -> None:
        brief = ProjectBriefV1.model_validate(
            {
                **_brief(),
                "operating_modes": "자동 모드",
                "sequence": "한 번 시작한 뒤 자동으로 계속 반복한다.",
            }
        )
        design = {
            "ioPoints": [
                {
                    "signalId": "start_btn",
                    "symbol": "START_BTN",
                    "kind": "digital_input",
                    "description": "시작 버튼",
                },
                {
                    "signalId": "system_running",
                    "symbol": "SYSTEM_RUNNING",
                    "kind": "internal_bit",
                    "description": "시스템 동작 중",
                },
                {
                    "signalId": "mode_auto",
                    "symbol": "MODE_AUTO",
                    "kind": "internal_bit",
                    "description": "자동 모드",
                },
            ],
            "operatingModes": [
                {
                    "modeId": "auto_mode",
                    "name": "자동",
                    "activation": {"signalId": "mode_auto", "state": "on"},
                }
            ],
            "controlNetworks": [],
            "ladderRungs": [
                {
                    "rungId": "run_out",
                    "condition": {
                        "kind": "contact",
                        "sourceType": "signal",
                        "sourceId": "start_btn",
                        "state": "on",
                    },
                    "action": {
                        "instruction": "OUT",
                        "targetType": "signal",
                        "targetId": "system_running",
                    },
                },
                {
                    "rungId": "auto_start",
                    "condition": {
                        "kind": "contact",
                        "sourceType": "signal",
                        "sourceId": "start_btn",
                        "state": "on",
                    },
                    "action": {
                        "instruction": "SET",
                        "targetType": "step",
                        "targetId": "auto_move",
                    },
                },
            ],
            "timers": [],
            "sequences": [
                {
                    "sequenceId": "auto_sequence",
                    "modeId": "auto_mode",
                    "restartPolicy": "continuous",
                    "startAll": [{"signalId": "start_btn", "state": "on"}],
                    "resetAll": [],
                    "steps": [{"stepId": "auto_move", "completeAll": []}],
                }
            ],
            "alarms": [],
            "recoveryRules": [],
            "safetyRules": [],
        }

        codes = {item["code"] for item in _requirement_diagnostics(design, brief)}
        self.assertIn("continuous_sequence_start_contract_uses_momentary_button", codes)
        self.assertIn("continuous_auto_requires_latched_run_state", codes)
        self.assertIn("continuous_sequence_cycle_depends_on_start_button", codes)

    def test_alarm_indicators_require_distinct_persistent_latches(self) -> None:
        design = {
            "ioPoints": [
                {"signalId": "cause_a", "kind": "digital_input"},
                {"signalId": "cause_b", "kind": "digital_input"},
                {"signalId": "shared_alarm", "kind": "internal_bit"},
                {"signalId": "alarm_a", "kind": "internal_bit"},
                {"signalId": "alarm_b", "kind": "internal_bit"},
            ],
            "operatingModes": [],
            "controlNetworks": [],
            "ladderRungs": [
                {
                    "rungId": "alarm_a_out",
                    "condition": {
                        "kind": "contact",
                        "sourceType": "signal",
                        "sourceId": "cause_a",
                        "state": "on",
                    },
                    "action": {
                        "instruction": "OUT",
                        "targetType": "signal",
                        "targetId": "alarm_a",
                    },
                },
                {
                    "rungId": "alarm_b_out",
                    "condition": {
                        "kind": "contact",
                        "sourceType": "signal",
                        "sourceId": "cause_b",
                        "state": "on",
                    },
                    "action": {
                        "instruction": "OUT",
                        "targetType": "signal",
                        "targetId": "alarm_b",
                    },
                },
            ],
            "timers": [],
            "sequences": [],
            "alarms": [
                {
                    "alarmId": "alarm_a",
                    "latchSignalId": "shared_alarm",
                    "affectedOutputSignalIds": [],
                    "triggerAll": [{"signalId": "cause_a", "state": "on"}],
                },
                {
                    "alarmId": "alarm_b",
                    "latchSignalId": "shared_alarm",
                    "affectedOutputSignalIds": [],
                    "triggerAll": [{"signalId": "cause_b", "state": "on"}],
                },
            ],
            "recoveryRules": [],
            "safetyRules": [],
        }

        diagnostics = _requirement_diagnostics(design, ProjectBriefV1.model_validate(_brief()))
        codes = [item["code"] for item in diagnostics]
        self.assertIn("alarm_indicators_require_distinct_cause_latches", codes)
        self.assertEqual(codes.count("alarm_indicator_not_persistent_until_reset"), 2)

    def test_unknown_alarm_latch_prefers_declared_indicator_bit(self) -> None:
        design = {
            "ioPoints": [
                {"signalId": "cause", "kind": "digital_input"},
                {"signalId": "alarm_cause", "kind": "internal_bit"},
                {"signalId": "shared_alarm", "kind": "internal_bit"},
            ],
            "operatingModes": [],
            "controlNetworks": [],
            "ladderRungs": [],
            "timers": [],
            "sequences": [],
            "alarms": [
                {
                    "alarmId": "alarm_cause",
                    "latchSignalId": "undeclared_latch",
                    "affectedOutputSignalIds": [],
                    "triggerAll": [{"signalId": "cause", "state": "on"}],
                }
            ],
            "recoveryRules": [],
            "safetyRules": [],
        }

        diagnostic = next(
            item
            for item in _reference_diagnostics(design)
            if item["code"] == "unknown_alarm_latch_reference"
        )
        self.assertIn("declared latchSignalId 'alarm_cause'", diagnostic["message"])
        self.assertNotIn("shared with another alarm", diagnostic["message"].split("preferred")[0])

    def test_set_owned_alarm_indicator_may_hold_through_its_own_contact(self) -> None:
        design = {
            "ioPoints": [
                {"signalId": "cause", "kind": "digital_input"},
                {"signalId": "alarm_cause", "kind": "internal_bit"},
            ],
            "operatingModes": [],
            "controlNetworks": [
                {
                    "networkId": "alarm_network",
                    "outputSignalId": "alarm_cause",
                    "permitAll": [{"signalId": "alarm_cause", "state": "on"}],
                    "modeId": None,
                    "stepId": None,
                }
            ],
            "ladderRungs": [
                {
                    "rungId": "alarm_set",
                    "condition": {
                        "kind": "contact",
                        "sourceType": "signal",
                        "sourceId": "cause",
                        "state": "on",
                    },
                    "action": {
                        "instruction": "SET",
                        "targetType": "signal",
                        "targetId": "alarm_cause",
                    },
                },
                {
                    "rungId": "alarm_out",
                    "condition": {
                        "kind": "contact",
                        "sourceType": "signal",
                        "sourceId": "alarm_cause",
                        "state": "on",
                    },
                    "action": {
                        "instruction": "OUT",
                        "targetType": "signal",
                        "targetId": "alarm_cause",
                    },
                },
            ],
            "timers": [],
            "sequences": [],
            "alarms": [
                {
                    "alarmId": "alarm_cause",
                    "latchSignalId": "alarm_cause",
                    "affectedOutputSignalIds": [],
                    "triggerAll": [{"signalId": "cause", "state": "on"}],
                }
            ],
            "recoveryRules": [],
            "safetyRules": [],
        }

        codes = {
            item["code"]
            for item in _requirement_diagnostics(
                design,
                ProjectBriefV1.model_validate(_brief()),
            )
        }
        self.assertNotIn("control_network_output_self_permit", codes)
        self.assertNotIn("output_self_dependency_has_no_start_path", codes)

    def test_recovery_diagnostic_lists_inverse_start_conditions(self) -> None:
        design = {
            "ioPoints": [
                {"signalId": "start", "kind": "digital_input"},
                {"signalId": "reset", "kind": "digital_input"},
            ],
            "operatingModes": [],
            "controlNetworks": [],
            "ladderRungs": [],
            "timers": [],
            "sequences": [
                {
                    "sequenceId": "manual_index",
                    "restartPolicy": "require_start_release",
                    "startAll": [{"signalId": "start", "state": "on"}],
                    "resetAll": [],
                    "steps": [],
                }
            ],
            "alarms": [],
            "recoveryRules": [
                {
                    "recoveryId": "operator_reset",
                    "whenAll": [{"signalId": "reset", "state": "on"}],
                    "resetAlarmIds": [],
                    "resetSequenceIds": ["manual_index"],
                }
            ],
            "safetyRules": [],
        }

        diagnostic = next(
            item
            for item in _reference_diagnostics(design)
            if item["code"] == "recovery_missing_inverse_start_condition"
        )
        self.assertEqual(diagnostic["path"], "/recoveryRules/0/whenAll")
        self.assertIn("('start', 'off')", diagnostic["message"])
        self.assertEqual(diagnostic["relatedIds"], ["manual_index"])

    def test_signal_latch_diagnostics_name_every_bad_rung(self) -> None:
        design = {
            "ioPoints": [
                {"signalId": "start", "kind": "digital_input"},
                {"signalId": "reset", "kind": "digital_input"},
                {"signalId": "ordinary_state", "kind": "internal_bit"},
                {"signalId": "alarm_latch", "kind": "internal_bit"},
            ],
            "operatingModes": [],
            "controlNetworks": [],
            "timers": [
                {
                    "timerId": "alarm_timer",
                    "enableAll": [],
                }
            ],
            "sequences": [],
            "alarms": [
                {
                    "alarmId": "timeout",
                    "latchSignalId": "alarm_latch",
                    "affectedOutputSignalIds": [],
                    "triggerAll": [{"signalId": "alarm_timer", "state": "on"}],
                }
            ],
            "recoveryRules": [
                {
                    "recoveryId": "operator_reset",
                    "whenAll": [{"signalId": "reset", "state": "on"}],
                    "resetAlarmIds": ["timeout"],
                    "resetSequenceIds": [],
                }
            ],
            "safetyRules": [],
            "ladderRungs": [
                {
                    "rungId": "bad_ordinary_reset",
                    "condition": {
                        "kind": "contact",
                        "sourceType": "signal",
                        "sourceId": "reset",
                        "state": "on",
                    },
                    "action": {
                        "instruction": "RST",
                        "targetType": "signal",
                        "targetId": "ordinary_state",
                    },
                },
                {
                    "rungId": "bad_alarm_reset_condition",
                    "condition": {
                        "kind": "contact",
                        "sourceType": "signal",
                        "sourceId": "start",
                        "state": "on",
                    },
                    "action": {
                        "instruction": "RST",
                        "targetType": "signal",
                        "targetId": "alarm_latch",
                    },
                },
                {
                    "rungId": "valid_timer_alarm_set",
                    "condition": {
                        "kind": "contact",
                        "sourceType": "timer",
                        "sourceId": "alarm_timer",
                        "state": "on",
                    },
                    "action": {
                        "instruction": "SET",
                        "targetType": "signal",
                        "targetId": "alarm_latch",
                    },
                },
            ],
        }

        diagnostics = _reference_diagnostics(design)
        by_code = {item["code"]: item for item in diagnostics}
        self.assertEqual(
            by_code["unreviewed_signal_reset"]["path"],
            "/ladderRungs/0/action",
        )
        self.assertEqual(
            by_code["unreviewed_signal_reset"]["received"]["rungId"],
            "bad_ordinary_reset",
        )
        self.assertEqual(
            by_code["alarm_reset_missing_recovery_conditions"]["path"],
            "/ladderRungs/1/condition",
        )
        self.assertEqual(
            by_code["alarm_reset_missing_recovery_conditions"]["relatedIds"],
            ["operator_reset"],
        )
        self.assertNotIn("alarm_set_missing_trigger_conditions", by_code)

    def test_sequence_diagnostics_report_start_and_each_missing_transition(self) -> None:
        design = {
            "ioPoints": [
                {"signalId": "start", "kind": "digital_input"},
                {"signalId": "ready", "kind": "digital_input"},
                {"signalId": "done", "kind": "digital_input"},
            ],
            "operatingModes": [],
            "controlNetworks": [],
            "ladderRungs": [],
            "timers": [],
            "sequences": [
                {
                    "sequenceId": "index_sequence",
                    "startAll": [{"signalId": "start", "state": "on"}],
                    "resetAll": [],
                    "restartPolicy": "automatic",
                    "steps": [
                        {
                            "stepId": "ready_step",
                            "completeAll": [{"signalId": "ready", "state": "on"}],
                        },
                        {
                            "stepId": "move_step",
                            "completeAll": [{"signalId": "done", "state": "on"}],
                        },
                    ],
                }
            ],
            "alarms": [],
            "recoveryRules": [],
            "safetyRules": [],
        }

        diagnostics = _reference_diagnostics(design)
        sequence_diagnostics = [
            item for item in diagnostics if item["code"].startswith("sequence_")
        ]
        self.assertEqual(len(sequence_diagnostics), 4)
        self.assertEqual(sequence_diagnostics[0]["path"], "/sequences/0/startAll")
        self.assertEqual(
            sequence_diagnostics[1]["received"]["targetId"],
            "move_step",
        )
        self.assertEqual(
            sequence_diagnostics[2]["received"]["targetType"],
            "sequence_reset",
        )
        self.assertEqual(
            sequence_diagnostics[3]["code"],
            "sequence_reset_rung_missing_or_incomplete",
        )

    def test_sequence_contract_diagnostics_name_closest_existing_sync_targets(self) -> None:
        contact = lambda signal_id, state: {
            "kind": "contact", "sourceType": "signal",
            "sourceId": signal_id, "state": state,
        }
        design = {
            "ioPoints": [
                {"signalId": signal_id, "kind": "internal_bit"}
                for signal_id in ("start", "ready", "stopped")
            ],
            "operatingModes": [], "controlNetworks": [], "timers": [],
            "alarms": [], "recoveryRules": [], "safetyRules": [],
            "sequences": [{
                "sequenceId": "sequence_one",
                "startAll": [
                    {"signalId": "start", "state": "on"},
                    {"signalId": "ready", "state": "on"},
                ],
                "resetAll": [
                    {"signalId": "stopped", "state": "on"},
                    {"signalId": "start", "state": "off"},
                ],
                "restartPolicy": "manual",
                "steps": [{"stepId": "wait_step", "completeAll": []}],
            }],
            "ladderRungs": [
                {
                    "rungId": "existing_start", "condition": contact("start", "on"),
                    "action": {"instruction": "SET", "targetType": "step", "targetId": "wait_step"},
                },
                {
                    "rungId": "ordinary_reset", "condition": contact("stopped", "on"),
                    "action": {"instruction": "SET", "targetType": "sequence_reset", "targetId": "sequence_one"},
                },
                {
                    "rungId": "unrelated_recovery_reset", "condition": contact("ready", "off"),
                    "action": {"instruction": "SET", "targetType": "sequence_reset", "targetId": "sequence_one"},
                },
            ],
        }

        diagnostics = _reference_diagnostics(design)
        start = next(item for item in diagnostics if item["code"] == "sequence_start_rung_missing_or_incomplete")
        reset = next(item for item in diagnostics if item["code"] == "sequence_reset_rung_missing_or_incomplete")
        self.assertEqual(start["received"]["candidateRungIds"], ["existing_start"])
        self.assertEqual(reset["received"]["candidateRungIds"], ["ordinary_reset"])
        self.assertIn("existing_start", start["relatedIds"])
        self.assertIn("ordinary_reset", reset["relatedIds"])

    def test_increment_reset_diagnostic_names_both_rungs_and_contacts(self) -> None:
        design = {
            "ioPoints": [
                {"signalId": "done", "kind": "digital_input"},
                {"signalId": "reset", "kind": "digital_input"},
            ],
            "operatingModes": [],
            "controlNetworks": [],
            "timers": [],
            "sequences": [],
            "alarms": [],
            "recoveryRules": [],
            "safetyRules": [],
            "ladderRungs": [
                {
                    "rungId": "count_increment",
                    "condition": {
                        "kind": "contact",
                        "sourceType": "signal",
                        "sourceId": "done",
                        "state": "on",
                    },
                    "action": {
                        "instruction": "INCP",
                        "destinationAddress": "D00100",
                    },
                },
                {
                    "rungId": "count_reset",
                    "condition": {
                        "kind": "contact",
                        "sourceType": "signal",
                        "sourceId": "reset",
                        "state": "on",
                    },
                    "action": {
                        "instruction": "MOV",
                        "sourceConstant": 0,
                        "destinationAddress": "D00100",
                    },
                },
            ],
        }

        diagnostic = next(
            item
            for item in _reference_diagnostics(design)
            if item["code"] == "increment_reset_not_mutually_exclusive"
        )
        self.assertEqual(diagnostic["path"], "/ladderRungs/0/condition")
        self.assertEqual(diagnostic["received"]["incrementRungId"], "count_increment")
        self.assertEqual(diagnostic["received"]["resetRungId"], "count_reset")
        self.assertEqual(diagnostic["relatedIds"], ["count_increment", "count_reset"])

    def test_host_check_accepts_incp_data_register_action(self) -> None:
        self.execute("design.inspect", {})
        changes = _design_changes()
        changes[5]["value"].append(
            {
                "rung_id": "increment_count",
                "description": "허가 입력마다 완료 횟수를 증가한다.",
                "condition": {
                    "kind": "contact",
                    "source_type": "signal",
                    "source_id": "permit",
                    "state": "on",
                },
                "action": {
                    "instruction": "INCP",
                    "destination_address": "D00100",
                },
            }
        )
        changes[5]["value"].append(
            {
                "rung_id": "reset_count",
                "description": "허가 해제 시 완료 횟수를 0으로 초기화한다.",
                "condition": {
                    "kind": "contact",
                    "source_type": "signal",
                    "source_id": "permit",
                    "state": "off",
                },
                "action": {
                    "instruction": "MOV",
                    "source_constant": 0,
                    "destination_address": "D00100",
                },
            }
        )
        applied = self.execute(
            "design.edit",
            {"expected_revision": 0, "changes": changes},
        )
        self.assertEqual(applied["status"], "applied")
        checked = self.execute("design.check", {"expected_revision": 1})
        self.assertEqual(checked["status"], "valid")

    def test_network_diagnostic_exposes_effective_safety_permits(self) -> None:
        design = {
            "ioPoints": [
                {"signalId": "run", "kind": "digital_input"},
                {"signalId": "estop", "kind": "digital_input"},
                {"signalId": "motor", "kind": "digital_output"},
            ],
            "operatingModes": [],
            "controlNetworks": [
                {
                    "networkId": "motor_network",
                    "permitAll": [{"signalId": "run", "state": "on"}],
                    "outputSignalId": "motor",
                }
            ],
            "ladderRungs": [
                {
                    "rungId": "motor_out",
                    "condition": {
                        "kind": "contact",
                        "sourceType": "signal",
                        "sourceId": "run",
                        "state": "on",
                    },
                    "action": {
                        "instruction": "OUT",
                        "targetType": "signal",
                        "targetId": "motor",
                    },
                }
            ],
            "timers": [],
            "sequences": [],
            "alarms": [],
            "recoveryRules": [],
            "safetyRules": [
                {
                    "ruleId": "estop_interlock",
                    "classification": "process_interlock",
                    "permitWhen": {"signalId": "estop", "state": "off"},
                    "affectedOutputSignalIds": ["motor"],
                }
            ],
        }

        diagnostic = next(
            item
            for item in _reference_diagnostics(design)
            if item["code"] == "control_network_out_missing_effective_permits"
        )
        self.assertEqual(diagnostic["path"], "/controlNetworks/0/permitAll")
        self.assertIn("('signal', 'estop', 'off')", diagnostic["message"])
        self.assertEqual(
            diagnostic["relatedIds"],
            ["motor_network", "estop_interlock"],
        )

    def test_network_cardinality_diagnostic_names_extra_out_rungs(self) -> None:
        design = {
            "ioPoints": [{"signalId": "run", "kind": "internal_bit"}],
            "operatingModes": [],
            "controlNetworks": [
                {
                    "networkId": "run_network",
                    "permitAll": [{"signalId": "run", "state": "on"}],
                    "outputSignalId": "run",
                }
            ],
            "ladderRungs": [
                {
                    "rungId": rung_id,
                    "condition": {
                        "kind": "contact",
                        "sourceType": "signal",
                        "sourceId": "run",
                        "state": "on",
                    },
                    "action": {
                        "instruction": "OUT",
                        "targetType": "signal",
                        "targetId": "run",
                    },
                }
                for rung_id in ("run_out_one", "run_out_two")
            ],
            "timers": [],
            "sequences": [],
            "alarms": [],
            "recoveryRules": [],
            "safetyRules": [],
        }

        diagnostic = next(
            item
            for item in _reference_diagnostics(design)
            if item["code"] == "control_network_out_cardinality_mismatch"
        )
        self.assertEqual(diagnostic["received"]["networkIds"], ["run_network"])
        self.assertEqual(
            diagnostic["received"]["outRungIds"],
            ["run_out_one", "run_out_two"],
        )

    def test_alarm_indicator_is_not_treated_as_affected_process_output(self) -> None:
        design = {
            "ioPoints": [
                {"signalId": "fault", "kind": "digital_input"},
                {"signalId": "alarm_latch", "kind": "internal_bit"},
                {"signalId": "alarm_fault", "kind": "digital_output"},
                {"signalId": "motor", "kind": "digital_output"},
            ],
            "operatingModes": [],
            "controlNetworks": [],
            "ladderRungs": [],
            "timers": [],
            "sequences": [],
            "alarms": [
                {
                    "alarmId": "alarm_fault",
                    "latchSignalId": "alarm_latch",
                    "affectedOutputSignalIds": ["alarm_fault"],
                    "triggerAll": [{"signalId": "fault", "state": "on"}],
                }
            ],
            "recoveryRules": [],
            "safetyRules": [],
        }

        diagnostics = _reference_diagnostics(design)
        self.assertEqual(
            diagnostics[0]["code"],
            "alarm_affected_output_is_alarm_indicator",
        )
        self.assertEqual(diagnostics[0]["relatedIds"], ["motor"])
        self.assertFalse(
            any(item["code"] == "alarm_output_missing_latch_off_interlock" for item in diagnostics)
        )

    def test_recovery_sequence_reset_diagnostic_names_required_contacts_and_candidate(self) -> None:
        when_all = [
            {"signalId": "reset", "state": "on"},
            {"signalId": "alarm_latch", "state": "on"},
            {"signalId": "start", "state": "off"},
        ]
        design = {
            "ioPoints": [
                {"signalId": "reset", "kind": "digital_input"},
                {"signalId": "alarm_latch", "kind": "internal_bit"},
                {"signalId": "start", "kind": "digital_input"},
            ],
            "operatingModes": [],
            "controlNetworks": [],
            "ladderRungs": [
                {
                    "rungId": "recovery_reset_sequence",
                    "condition": {
                        "kind": "all_of",
                        "terms": [
                            {
                                "kind": "contact",
                                "sourceType": "signal",
                                "sourceId": "reset",
                                "state": "on",
                            },
                            {
                                "kind": "contact",
                                "sourceType": "signal",
                                "sourceId": "alarm_latch",
                                "state": "off",
                            },
                        ],
                    },
                    "action": {
                        "instruction": "SET",
                        "targetType": "sequence_reset",
                        "targetId": "index_sequence",
                    },
                }
            ],
            "timers": [],
            "sequences": [
                {
                    "sequenceId": "index_sequence",
                    "modeId": None,
                    "restartPolicy": "continuous",
                    "startAll": [],
                    "resetAll": [{"signalId": "start", "state": "off"}],
                    "steps": [
                        {
                            "stepId": "move_step",
                            "completeAll": [],
                        }
                    ],
                }
            ],
            "alarms": [],
            "recoveryRules": [
                {
                    "recoveryId": "alarm_recovery",
                    "resetAlarmIds": [],
                    "resetSequenceIds": ["index_sequence"],
                    "whenAll": when_all,
                }
            ],
            "safetyRules": [],
        }

        diagnostic = next(
            item
            for item in _reference_diagnostics(design)
            if item["code"] == "recovery_sequence_reset_rung_missing_or_incomplete"
        )
        self.assertEqual(
            diagnostic["received"]["candidateRungIds"],
            ["recovery_reset_sequence"],
        )
        self.assertEqual(
            diagnostic["received"]["requiredContacts"],
            [
                ("signal", "reset", "on"),
                ("signal", "alarm_latch", "on"),
                ("signal", "start", "off"),
            ],
        )
        self.assertIn("recovery_reset_sequence", diagnostic["relatedIds"])
        reset_diagnostic = next(
            item
            for item in _reference_diagnostics(design)
            if item["code"] == "sequence_reset_rung_missing_or_incomplete"
        )
        self.assertEqual(
            reset_diagnostic["received"]["requiredContacts"],
            [("signal", "start", "off")],
        )


if __name__ == "__main__":
    unittest.main()
