from __future__ import annotations

import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from controlstudio_host.agent_client import _safe_trusted_context
from controlstudio_host.api_models import SendMessageRequestV1
from controlstudio_host.app import (
    _acknowledged_terminal_source_run_change,
    _authorized_exact_operand_retry_evidence_compaction,
    _authorized_exact_operand_retry_route_refresh,
    _expired_exact_operand_reproposal_callback_permitted,
    _matching_change_outcome_supplemental_evidence,
    _rejected_exact_operand_reauthorization_permitted,
    _require_offline_operand_mission_plan,
    _validate_engineering_mission_progression,
)
from controlstudio_host.change_outcome import change_outcome_projection
from controlstudio_host.models import (
    ChangeRiskLevel,
    ChangeTargetIdentity,
    ChangeTransaction,
    ChangeTransactionState,
    EngineeringMission,
    Message,
    MessageRole,
)


class OfflineOperandMissionTests(unittest.TestCase):
    @staticmethod
    def _request(**overrides: object) -> dict[str, object]:
        return {
            "content": "Use the Host-owned mission.",
            "operationId": "offline-operand-mission-1",
            "workflow": "engineering",
            "changeRequestScope": "offline_candidate_revision",
            "verificationScope": "offline_native_reopen",
            "offlineOperandMission": {
                "schemaVersion": "1",
                "programId": "ld:55e388e7aea7a8ce",
                "programName": "MainProgram",
                "expectedOperand": "P0020",
                "replacementOperand": "P0021",
                "tokenOffsetSource": "fresh_ld_evidence",
            },
            **overrides,
        }

    @classmethod
    def _rejected_change(cls) -> ChangeTransaction:
        now = datetime.now(timezone.utc)
        return ChangeTransaction(
            id="00000000-0000-4000-8000-000000000201",
            operation_id="rejected-operand-plan",
            conversation_id="00000000-0000-4000-8000-000000000202",
            run_id="00000000-0000-4000-8000-000000000203",
            state=ChangeTransactionState.REJECTED,
            target=ChangeTargetIdentity(
                workspace_id="workspace",
                revision_id="revision",
                artifact_hash="a" * 64,
                source_artifact_hash="b" * 64,
                project_path="C:/workspace/project.xpj",
                vendor="LS",
                engineering_system="XG5000",
            ),
            mutation_scope="workspace:workspace",
            risk_level=ChangeRiskLevel.MEDIUM,
            intent="Replace one exact operand.",
            plan={
                "target": {
                    "programId": "ld:55e388e7aea7a8ce",
                    "programName": "MainProgram",
                    "tokenOffset": 18,
                },
                "current": {"value": "P0020"},
                "proposed": {"value": "P0021"},
            },
            plan_digest="c" * 64,
            requested_by_actor_id="user",
            approval_expires_at=now,
            result={},
            created_at=now,
            updated_at=now,
            completed_at=now,
        )

    @classmethod
    def _user_message(cls, *, system: bool = False) -> Message:
        now = datetime.now(timezone.utc)
        return Message(
            id="00000000-0000-4000-8000-000000000204",
            conversation_id="00000000-0000-4000-8000-000000000202",
            sequence=1,
            role=MessageRole.SYSTEM if system else MessageRole.USER,
            content="Please create the same exact offline operand candidate again.",
            metadata={"engineering_mission_continue": True} if system else {},
            created_at=now,
        )

    @classmethod
    def _rejected_outcome(cls, transaction: ChangeTransaction) -> dict[str, object]:
        return {
            "state": "REJECTED",
            "disposition": "rejected",
            "transactionId": transaction.id,
            "planDigest": transaction.plan_digest,
        }

    def test_typed_operand_mission_requires_offline_candidate_reopen_scope(self) -> None:
        request = SendMessageRequestV1.model_validate(self._request())
        self.assertEqual(request.offline_operand_mission.program_id, "ld:55e388e7aea7a8ce")
        with self.assertRaises(ValidationError):
            SendMessageRequestV1.model_validate(self._request(changeRequestScope="analysis_only"))

    def test_agent_context_preserves_only_validated_typed_operand_mission(self) -> None:
        context = _safe_trusted_context(
            {
                "workflow": "engineering",
                "change_request_scope": "offline_candidate_revision",
                "verification_scope": "offline_native_reopen",
                "offline_operand_mission": self._request()["offlineOperandMission"],
            },
            knowledge_search_available=False,
            change_planner_available=True,
            mne_change_planner_available=False,
        )
        assert context is not None
        self.assertEqual(
            context["offline_operand_mission"]["tokenOffsetSource"], "fresh_ld_evidence"
        )

    def test_host_rejects_agent_plan_that_deviates_from_typed_mission(self) -> None:
        mission = self._request()["offlineOperandMission"]
        values = {
            "target": {
                "programId": "ld:55e388e7aea7a8ce",
                "programName": "MainProgram",
                "tokenOffset": 18,
            },
            "current": {"value": "P0020"},
            "proposed": {"value": "P0021"},
        }
        _require_offline_operand_mission_plan(mission, values)
        values["proposed"] = {"value": "P0022"}
        with self.assertRaisesRegex(ValueError, "Host-owned"):
            _require_offline_operand_mission_plan(mission, values)

    def test_rejected_reauthorization_requires_user_and_identical_operand_plan(self) -> None:
        transaction = self._rejected_change()
        outcome = self._rejected_outcome(transaction)
        reauthorization = self._request()["offlineOperandMission"]
        assert isinstance(reauthorization, dict)
        self.assertTrue(
            _rejected_exact_operand_reauthorization_permitted(
                request_message=self._user_message(),
                linked_change=transaction,
                change_outcome=outcome,
                offline_operand_mission=reauthorization,
            )
        )
        self.assertFalse(
            _rejected_exact_operand_reauthorization_permitted(
                request_message=self._user_message(system=True),
                linked_change=transaction,
                change_outcome=outcome,
                offline_operand_mission=reauthorization,
            )
        )
        foreign = {**reauthorization, "replacementOperand": "P0022"}
        self.assertFalse(
            _rejected_exact_operand_reauthorization_permitted(
                request_message=self._user_message(),
                linked_change=transaction,
                change_outcome=outcome,
                offline_operand_mission=foreign,
            )
        )

    def test_rejected_reauthorization_accepts_only_absent_legacy_program_name(
        self,
    ) -> None:
        transaction = self._rejected_change()
        target = transaction.plan["target"]
        target.pop("programName")
        outcome = self._rejected_outcome(transaction)
        reauthorization = self._request()["offlineOperandMission"]
        assert isinstance(reauthorization, dict)

        self.assertTrue(
            _rejected_exact_operand_reauthorization_permitted(
                request_message=self._user_message(),
                linked_change=transaction,
                change_outcome=outcome,
                offline_operand_mission=reauthorization,
            )
        )

        target["programName"] = "DifferentProgram"
        self.assertFalse(
            _rejected_exact_operand_reauthorization_permitted(
                request_message=self._user_message(),
                linked_change=transaction,
                change_outcome=outcome,
                offline_operand_mission=reauthorization,
            )
        )

        target.pop("programName")
        transaction.plan["planKind"] = "mne_instruction_patch"
        self.assertFalse(
            _rejected_exact_operand_reauthorization_permitted(
                request_message=self._user_message(),
                linked_change=transaction,
                change_outcome=outcome,
                offline_operand_mission=reauthorization,
            )
        )

    def test_expired_exact_operand_checkpoint_allows_only_fresh_reproposal_gate(
        self,
    ) -> None:
        mission = {
            "objective": "MainProgram LD P0020 operand becomes P0021.",
            "status": "active",
            "stopReason": "continuing",
            "activeStepId": "proposal",
            "steps": [
                {
                    "stepId": "hardware",
                    "title": "hardware(inspect_project_hardware_context)",
                    "status": "completed",
                    "evidenceRefs": ["hardware-evidence"],
                },
                {
                    "stepId": "target",
                    "title": "target(analyze_change_target)",
                    "status": "completed",
                    "evidenceRefs": ["target-evidence"],
                },
                {
                    "stepId": "proposal",
                    "title": "proposal(propose_offline_change)",
                    "status": "active",
                    "evidenceRefs": ["snapshot-evidence"],
                },
            ],
        }
        expired = {"state": "EXPIRED", "disposition": "expired"}
        self.assertTrue(
            _expired_exact_operand_reproposal_callback_permitted(mission, expired)
        )
        internal_m_mission = {
            **mission,
            "objective": "MainProgram LD M0010 operand becomes M0011.",
        }
        self.assertTrue(
            _expired_exact_operand_reproposal_callback_permitted(
                internal_m_mission,
                expired,
            )
        )
        clean_expired_wait = {
            **mission,
            "status": "waiting_for_approval",
            "stopReason": "needs_human_approval",
            "activeStepId": None,
            "steps": [
                *mission["steps"][:2],
                {**mission["steps"][2], "status": "pending"},
            ],
        }
        self.assertTrue(
            _expired_exact_operand_reproposal_callback_permitted(
                clean_expired_wait,
                expired,
            )
        )
        self.assertTrue(
            _expired_exact_operand_reproposal_callback_permitted(
                clean_expired_wait,
                {
                    "state": "FAILED",
                    "disposition": "failed",
                    "errorCode": "native_change_not_applied",
                },
            )
        )
        self.assertTrue(
            _expired_exact_operand_reproposal_callback_permitted(
                clean_expired_wait,
                {
                    "state": "ROLLED_BACK",
                    "disposition": "rolled_back",
                    "errorCode": "explicit_recovery_rolled_back",
                    "verificationEvidenceDigest": "a" * 64,
                },
            )
        )
        self.assertFalse(
            _expired_exact_operand_reproposal_callback_permitted(
                clean_expired_wait,
                {
                    "state": "ROLLED_BACK",
                    "disposition": "rolled_back",
                    "errorCode": "explicit_recovery_rolled_back",
                },
            )
        )
        self.assertFalse(
            _expired_exact_operand_reproposal_callback_permitted(
                clean_expired_wait,
                {
                    "state": "FAILED",
                    "disposition": "failed",
                    "errorCode": "native_change_outcome_uncertain",
                },
            )
        )
        self.assertFalse(
            _expired_exact_operand_reproposal_callback_permitted(
                mission,
                {"state": "REJECTED", "disposition": "rejected"},
            )
        )
        self.assertTrue(
            _expired_exact_operand_reproposal_callback_permitted(
                mission,
                {"state": "REJECTED", "disposition": "rejected"},
                rejected_reauthorized=True,
            )
        )
        self.assertFalse(
            _expired_exact_operand_reproposal_callback_permitted(
                mission,
                {"state": "PENDING_APPROVAL", "disposition": "pending"},
            )
        )
        non_active = {**mission, "activeStepId": "target"}
        self.assertFalse(
            _expired_exact_operand_reproposal_callback_permitted(non_active, expired)
        )
        malformed_wait = {
            **clean_expired_wait,
            "stopReason": "continuing",
        }
        self.assertFalse(
            _expired_exact_operand_reproposal_callback_permitted(
                malformed_wait,
                expired,
            )
        )

    def test_acknowledged_terminal_source_run_recovers_only_the_exact_missing_link(self) -> None:
        now = datetime.now(timezone.utc)
        source_run_id = "00000000-0000-4000-8000-000000000203"
        old_search = "old-search"
        old_hardware = "old-hardware"
        old_target = "old-target"
        old_snapshot = "old-snapshot"
        fresh_search = "f0986adc-ffb3-5e3e-be98-17c639fbccc2"
        previous = {
            "objective": "MainProgram LD P0020 operand becomes P0021.",
            "status": "active",
            "completionCriteria": ["exact offline candidate"],
            "activeStepId": "proposal",
            "blockingQuestions": [],
            "nextAction": "Call propose_offline_change.",
            "stopReason": "continuing",
            "steps": [
                {"stepId": "search", "title": "MainProgram P0020 location", "status": "completed", "outcome": "old", "evidenceRefs": [old_search]},
                {"stepId": "hardware", "title": "hardware(inspect_project_hardware_context)", "status": "completed", "outcome": "old", "evidenceRefs": [old_hardware]},
                {"stepId": "target", "title": "target(analyze_change_target)", "status": "completed", "outcome": "old", "evidenceRefs": [old_target]},
                {"stepId": "proposal", "title": "proposal(propose_offline_change)", "status": "active", "outcome": None, "evidenceRefs": [old_snapshot]},
                {"stepId": "verify", "title": "verify", "status": "pending", "outcome": None, "evidenceRefs": []},
            ],
        }
        mission = EngineeringMission(
            conversation_id="00000000-0000-4000-8000-000000000202",
            workspace_id="workspace",
            revision_id="revision",
            revision=4,
            engineering_mission=previous,
            digest="a" * 64,
            updated_by_run_id=source_run_id,
            change_transaction_id=None,
            created_at=now,
            updated_at=now,
        )
        terminal = self._rejected_change().model_copy(
            update={
                "run_id": source_run_id,
                "state": ChangeTransactionState.ROLLED_BACK,
                "error_code": "explicit_recovery_rolled_back",
                "result": {"verification": {"receipt": "verified"}},
                "receipt_acknowledged_at": now,
            }
        )
        resolved = _acknowledged_terminal_source_run_change(mission, [terminal])
        self.assertEqual(resolved, terminal)
        self.assertTrue(
            _expired_exact_operand_reproposal_callback_permitted(
                previous,
                change_outcome_projection(resolved),
            )
        )

        current = {
            **previous,
            "activeStepId": "hardware",
            "nextAction": "Execute the next durable mission step: hardware(inspect_project_hardware_context)",
            "steps": [
                {**previous["steps"][0], "outcome": "fresh", "evidenceRefs": [fresh_search]},
                {**previous["steps"][1], "status": "active", "outcome": None, "evidenceRefs": []},
                {**previous["steps"][2], "status": "pending", "outcome": None, "evidenceRefs": []},
                {**previous["steps"][3], "status": "pending", "evidenceRefs": []},
                previous["steps"][4],
            ],
        }
        prior = {
            old_search: {"evidenceId": old_search, "toolName": "project.search"},
            old_hardware: {"evidenceId": old_hardware, "toolName": "project.hardware_context"},
            old_target: {"evidenceId": old_target, "toolName": "program.change_target_analysis"},
            old_snapshot: {"evidenceId": old_snapshot, "toolName": "project.snapshot"},
        }
        fresh = {
            fresh_search: {
                "evidenceId": fresh_search,
                "toolName": "project.search",
                "receipt": {"schema": "controlstudio-structural-analysis-evidence/v1"},
            }
        }
        removed, reopened = _authorized_exact_operand_retry_route_refresh(
            previous,
            current,
            retry_permitted=True,
            prior_evidence=prior,
            current_run_evidence=fresh,
        )
        self.assertEqual(removed, {old_search, old_hardware, old_target, old_snapshot})
        self.assertEqual(reopened, {"search", "hardware", "target"})
        _validate_engineering_mission_progression(
            previous,
            current,
            current_run_evidence_refs={fresh_search},
            allowed_removed_evidence_refs=removed,
            allowed_reopened_step_ids=reopened,
        )

        ambiguous = terminal.model_copy(update={"id": "00000000-0000-4000-8000-000000000205"})
        mismatched = terminal.model_copy(update={"run_id": "00000000-0000-4000-8000-000000000206"})
        unsafe = terminal.model_copy(update={"receipt_acknowledged_at": None})
        self.assertIsNone(_acknowledged_terminal_source_run_change(mission, [terminal, ambiguous]))
        self.assertIsNone(_acknowledged_terminal_source_run_change(mission, [mismatched]))
        self.assertIsNone(_acknowledged_terminal_source_run_change(mission, [unsafe]))

    def test_reproposal_pending_checkpoint_omits_old_expired_supplemental(self) -> None:
        old_expired_evidence = {
            "evidenceId": "old-expired-outcome",
            "receipt": {
                "schema": "controlstudio-change-outcome-evidence/v1",
                "transactionId": "expired-transaction",
                "state": "EXPIRED",
            },
        }
        self.assertIsNone(
            _matching_change_outcome_supplemental_evidence(
                "new-pending-transaction",
                old_expired_evidence,
            )
        )
        self.assertEqual(
            _matching_change_outcome_supplemental_evidence(
                "expired-transaction",
                old_expired_evidence,
            ),
            old_expired_evidence,
        )

    def test_exact_retry_compacts_only_superseded_active_proposal_trio(self) -> None:
        old_hardware = "00000000-0000-4000-8000-000000000301"
        old_target = "00000000-0000-4000-8000-000000000302"
        old_snapshot = "00000000-0000-4000-8000-000000000303"
        fresh_hardware = "00000000-0000-4000-8000-000000000304"
        previous = {
            "objective": "MainProgram LD P0020 operand becomes P0021.",
            "status": "active",
            "completionCriteria": ["exact offline candidate"],
            "activeStepId": "proposal",
            "blockingQuestions": [],
            "nextAction": "Call propose_offline_change.",
            "stopReason": "continuing",
            "steps": [
                {
                    "stepId": "search",
                    "title": "search",
                    "status": "completed",
                    "outcome": "located",
                    "evidenceRefs": ["search-evidence"],
                },
                {
                    "stepId": "proposal",
                    "title": "proposal(propose_offline_change)",
                    "status": "active",
                    "outcome": None,
                    "evidenceRefs": [old_hardware, old_target, old_snapshot],
                },
            ],
        }
        current = {
            **previous,
            "nextAction": (
                "Call analyze_change_target with the refreshed current-run hardware "
                "context before proposing the exact named-program operand candidate."
            ),
            "steps": [
                previous["steps"][0],
                {
                    **previous["steps"][1],
                    "evidenceRefs": [fresh_hardware],
                },
            ],
        }
        prior_evidence = {
            old_hardware: {
                "evidenceId": old_hardware,
                "toolName": "project.hardware_context",
            },
            old_target: {
                "evidenceId": old_target,
                "toolName": "program.change_target_analysis",
            },
            old_snapshot: {
                "evidenceId": old_snapshot,
                "toolName": "project.snapshot",
            },
            "search-evidence": {
                "evidenceId": "search-evidence",
                "toolName": "project.search",
            },
        }
        current_run_evidence = {
            fresh_hardware: {
                "evidenceId": fresh_hardware,
                "toolName": "project.hardware_context",
                "receipt": {
                    "schema": "controlstudio-hardware-context-evidence/v1",
                    "contextDigest": "a" * 64,
                },
            }
        }
        allowed = _authorized_exact_operand_retry_evidence_compaction(
            previous,
            current,
            retry_permitted=True,
            prior_evidence=prior_evidence,
            current_run_evidence=current_run_evidence,
        )
        self.assertEqual(allowed, {old_hardware, old_target, old_snapshot})
        _validate_engineering_mission_progression(
            previous,
            current,
            current_run_evidence_refs={fresh_hardware},
            allowed_removed_evidence_refs=allowed,
        )
        with self.assertRaisesRegex(ValueError, "lost evidence"):
            _validate_engineering_mission_progression(
                previous,
                current,
                current_run_evidence_refs={fresh_hardware},
            )

        prior_evidence[old_target] = {
            "evidenceId": old_target,
            "toolName": "program.read_ld",
        }
        self.assertEqual(
            _authorized_exact_operand_retry_evidence_compaction(
                previous,
                current,
                retry_permitted=True,
                prior_evidence=prior_evidence,
                current_run_evidence=current_run_evidence,
            ),
            set(),
        )

    def test_exact_retry_fresh_search_reopens_only_read_route(self) -> None:
        old_search = "00000000-0000-4000-8000-000000000311"
        old_hardware = "00000000-0000-4000-8000-000000000312"
        old_target = "00000000-0000-4000-8000-000000000313"
        old_snapshot = "00000000-0000-4000-8000-000000000314"
        fresh_search = "00000000-0000-4000-8000-000000000315"
        previous = {
            "objective": "MainProgram LD P0020 operand becomes P0021.",
            "status": "active",
            "completionCriteria": ["exact offline candidate"],
            "activeStepId": "proposal",
            "blockingQuestions": [],
            "nextAction": "Call propose_offline_change.",
            "stopReason": "continuing",
            "steps": [
                {"stepId": "search", "title": "MainProgram P0020 location", "status": "completed", "outcome": "old", "evidenceRefs": [old_search]},
                {"stepId": "hardware", "title": "hardware(inspect_project_hardware_context)", "status": "completed", "outcome": "old", "evidenceRefs": [old_hardware]},
                {"stepId": "target", "title": "target(analyze_change_target)", "status": "completed", "outcome": "old", "evidenceRefs": [old_target]},
                {"stepId": "proposal", "title": "proposal(propose_offline_change)", "status": "active", "outcome": None, "evidenceRefs": [old_snapshot]},
                {"stepId": "verify", "title": "verify", "status": "pending", "outcome": None, "evidenceRefs": []},
            ],
        }
        current = {
            **previous,
            "activeStepId": "hardware",
            "nextAction": "Execute the next durable mission step: hardware(inspect_project_hardware_context)",
            "steps": [
                {**previous["steps"][0], "outcome": "fresh", "evidenceRefs": [fresh_search]},
                {**previous["steps"][1], "status": "active", "outcome": None, "evidenceRefs": []},
                {**previous["steps"][2], "status": "pending", "outcome": None, "evidenceRefs": []},
                {**previous["steps"][3], "status": "pending", "evidenceRefs": []},
                previous["steps"][4],
            ],
        }
        prior = {
            old_search: {"evidenceId": old_search, "toolName": "project.search"},
            old_hardware: {"evidenceId": old_hardware, "toolName": "project.hardware_context"},
            old_target: {"evidenceId": old_target, "toolName": "program.change_target_analysis"},
            old_snapshot: {"evidenceId": old_snapshot, "toolName": "project.snapshot"},
        }
        fresh = {
            fresh_search: {
                "evidenceId": fresh_search,
                "toolName": "project.search",
                "receipt": {"schema": "controlstudio-structural-analysis-evidence/v1"},
            }
        }
        removed, reopened = _authorized_exact_operand_retry_route_refresh(
            previous, current, retry_permitted=True,
            prior_evidence=prior, current_run_evidence=fresh,
        )
        self.assertEqual(removed, {old_search, old_hardware, old_target, old_snapshot})
        self.assertEqual(reopened, {"search", "hardware", "target"})
        _validate_engineering_mission_progression(
            previous, current, current_run_evidence_refs={fresh_search},
            allowed_removed_evidence_refs=removed,
            allowed_reopened_step_ids=reopened,
        )
        self.assertEqual(
            _authorized_exact_operand_retry_route_refresh(
                previous, current, retry_permitted=False,
                prior_evidence=prior, current_run_evidence=fresh,
            ),
            (set(), set()),
        )
        malformed = {**current, "activeStepId": "target"}
        self.assertEqual(
            _authorized_exact_operand_retry_route_refresh(
                previous, malformed, retry_permitted=True,
                prior_evidence=prior, current_run_evidence=fresh,
            ),
            (set(), set()),
        )
if __name__ == "__main__":
    unittest.main()
