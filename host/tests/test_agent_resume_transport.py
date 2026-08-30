from __future__ import annotations

import hashlib
import json
import unittest
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, cast

from controlstudio_host.agent_client import (
    _EXACT_OFFLINE_OPERAND_CONTINUATION_PROMPT,
    AgentSupervisor,
    SubprocessAgentClient,
    build_historical_evidence_bundle,
)
from controlstudio_host.agent_protocol import MAX_PROTOCOL_LINE_BYTES, encode_message
from controlstudio_host.execution_broker import ExecutionBroker
from controlstudio_host.model_connection import (
    MODEL_PROTOCOL,
    ModelSecrets,
    StoredModelConnection,
)
from controlstudio_host.models import Message, MessageRole


class _CapturedRun(RuntimeError):
    pass


class _Broker:
    async def agent_tool_catalog(self, **_kwargs: Any) -> list[Any]:
        return []


class _Supervisor:
    def __init__(self) -> None:
        self.payload: dict[str, Any] | None = None
        self.historical_evidence: Mapping[str, Any] | None = None
        self.agent_checkpoint: Mapping[str, Any] | None = None

    async def run(
        self,
        payload: Mapping[str, Any],
        *,
        on_message: Any,
        historical_evidence: Mapping[str, Any] | None,
        agent_checkpoint: Mapping[str, Any] | None,
    ) -> None:
        del on_message
        self.payload = dict(payload)
        self.historical_evidence = historical_evidence
        self.agent_checkpoint = agent_checkpoint
        raise _CapturedRun


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class AgentResumeTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_checkpoint_omits_history_and_large_state_uses_separate_frames(
        self,
    ) -> None:
        evidence_id = "00000000-0000-4000-8000-000000000001"
        mission = {
            "schemaVersion": "1",
            "objective": "Continue the durable mission.",
            "status": "active",
            "completionCriteria": ["Return a verified result."],
            "steps": [{"evidenceRefs": [evidence_id]}],
            "activeStepId": None,
            "blockingQuestions": [],
            "nextAction": "Continue.",
            "stopReason": "continuing",
        }
        mission_digest = hashlib.sha256(_canonical(mission)).hexdigest()
        bundle = build_historical_evidence_bundle(
            mission_digest=mission_digest,
            evidence={
                evidence_id: (
                    "origin-run",
                    {
                        "evidenceId": evidence_id,
                        "toolName": "project.hardware_context",
                        "summary": "x" * 1_000_000,
                    },
                )
            },
        )
        checkpoint = {
            "schemaVersion": "1",
            "cycle": 1,
            "missionDigest": mission_digest,
            "messages": [{"content": "y" * 1_000_000}],
        }
        checkpoint_digest = hashlib.sha256(_canonical(checkpoint)).hexdigest()
        history: Sequence[Message] = [
            Message(
                id="message-1",
                conversation_id="conversation-1",
                sequence=1,
                role=MessageRole.USER,
                content="This history is already inside the checkpoint.",
                metadata={},
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        ]
        supervisor = _Supervisor()
        client = SubprocessAgentClient(
            StoredModelConnection(
                protocol=MODEL_PROTOCOL,
                base_url="https://model.invalid/v1",
                model="unused",
                timeout_seconds=1.0,
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                capability_verified_at=None,
                secrets=ModelSecrets(api_key=None, custom_headers={}),
            ),
            cast(ExecutionBroker, _Broker()),
            cast(AgentSupervisor, supervisor),
        )

        with self.assertRaises(_CapturedRun):
            await client.run(
                prompt="Continue.",
                history=history,
                conversation_id="conversation-1",
                run_id="run-2",
                trusted_context={
                    "engineering_mission": mission,
                    "historical_evidence": bundle,
                },
                agent_checkpoint=checkpoint,
            )

        assert supervisor.payload is not None
        self.assertEqual(supervisor.payload["history"], [])
        self.assertNotIn("agent_checkpoint", supervisor.payload)
        self.assertNotIn(
            "historical_evidence",
            supervisor.payload["trusted_context"],
        )
        self.assertEqual(supervisor.historical_evidence, bundle)
        self.assertEqual(supervisor.agent_checkpoint, checkpoint)
        self.assertLess(
            len(
                encode_message(
                    "historical_evidence.sync.item",
                    {
                        "bundleDigest": bundle["bundleDigest"],
                        "index": 0,
                        "item": bundle["items"][0],
                    },
                )
            ),
            MAX_PROTOCOL_LINE_BYTES,
        )
        self.assertLess(
            len(
                encode_message(
                    "engineering_checkpoint.sync",
                    {
                        "checkpoint": checkpoint,
                        "checkpointDigest": checkpoint_digest,
                    },
                )
            ),
            MAX_PROTOCOL_LINE_BYTES,
        )
        self.assertLess(
            len(encode_message("run.start", supervisor.payload)),
            MAX_PROTOCOL_LINE_BYTES,
        )

    async def test_exact_offline_operand_continuation_uses_compatible_wire_prompt(
        self,
    ) -> None:
        supervisor = _Supervisor()
        client = SubprocessAgentClient(
            StoredModelConnection(
                protocol=MODEL_PROTOCOL,
                base_url="https://model.invalid/v1",
                model="unused",
                timeout_seconds=1.0,
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                capability_verified_at=None,
                secrets=ModelSecrets(api_key=None, custom_headers={}),
            ),
            cast(ExecutionBroker, _Broker()),
            cast(AgentSupervisor, supervisor),
        )
        mission = {
            "schemaVersion": "1",
            "objective": "MainProgram의 P0020을 P0021로 바꾸는 후보를 제안합니다.",
            "status": "active",
            "completionCriteria": ["승인 대기 후보를 만듭니다."],
            "steps": [
                {
                    "stepId": "propose_offline_change",
                    "title": "승인 전 오프라인 후보 변경 제안",
                    "status": "active",
                    "evidenceRefs": [],
                }
            ],
            "activeStepId": "propose_offline_change",
            "blockingQuestions": [],
            "nextAction": "후보를 제안합니다.",
            "stopReason": "continuing",
        }

        with self.assertRaises(_CapturedRun):
            await client.run(
                prompt="MainProgram의 P0020을 P0021로 바꿔 주세요.",
                history=[],
                conversation_id="conversation-exact",
                run_id="run-exact",
                trusted_context={
                    "workflow": "engineering",
                    "change_request_scope": "offline_candidate_revision",
                    "verification_scope": "offline_native_reopen",
                    "offline_operand_mission": {
                        "schemaVersion": "1",
                        "programId": "MainProgram",
                        "programName": "MainProgram",
                        "expectedOperand": "P0020",
                        "replacementOperand": "P0021",
                        "tokenOffsetSource": "fresh_ld_evidence",
                    },
                    "engineering_mission": mission,
                },
            )

        assert supervisor.payload is not None
        self.assertEqual(
            supervisor.payload["prompt"],
            _EXACT_OFFLINE_OPERAND_CONTINUATION_PROMPT,
        )
        self.assertEqual(
            supervisor.payload["trusted_context"]["engineering_mission"],
            mission,
        )

    async def test_unbound_engineering_prompt_is_not_rewritten(self) -> None:
        supervisor = _Supervisor()
        client = SubprocessAgentClient(
            StoredModelConnection(
                protocol=MODEL_PROTOCOL,
                base_url="https://model.invalid/v1",
                model="unused",
                timeout_seconds=1.0,
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                capability_verified_at=None,
                secrets=ModelSecrets(api_key=None, custom_headers={}),
            ),
            cast(ExecutionBroker, _Broker()),
            cast(AgentSupervisor, supervisor),
        )
        prompt = "MainProgram의 P0020을 P0021로 바꿔 주세요."

        with self.assertRaises(_CapturedRun):
            await client.run(
                prompt=prompt,
                history=[],
                conversation_id="conversation-unbound",
                run_id="run-unbound",
                trusted_context={"workflow": "engineering"},
            )

        assert supervisor.payload is not None
        self.assertEqual(supervisor.payload["prompt"], prompt)


if __name__ == "__main__":
    unittest.main()
