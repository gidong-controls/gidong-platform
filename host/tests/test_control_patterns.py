from __future__ import annotations

import json
import re
from dataclasses import FrozenInstanceError

import pytest

from controlstudio_host.control_patterns import (
    MANDATORY_BLOCKING_DECISION_IDS,
    iter_builtin_knowledge_documents,
    iter_builtin_patterns,
    pattern_search_text,
    pattern_to_dict,
    pattern_to_knowledge_document,
)

EXPECTED_PATTERN_IDS = {
    "motor.dol",
    "motor.reversing",
    "cylinder.double_solenoid",
    "drive.vfd",
    "axis.servo",
    "alarm.lifecycle",
    "operation.mode_manager",
    "initialization_recovery",
    "sequence.step_machine",
    "interlock.resource_owner",
    "command.request_ack_done",
    "analog.scaled_measurement",
}


def test_builtin_pattern_catalog_is_stable_complete_and_unique() -> None:
    patterns = tuple(iter_builtin_patterns())

    assert {pattern.pattern_id for pattern in patterns} == EXPECTED_PATTERN_IDS
    assert len({(pattern.pattern_id, pattern.revision) for pattern in patterns}) == len(patterns)


def test_every_pattern_has_the_engineering_contract_and_mandatory_blockers() -> None:
    for pattern in iter_builtin_patterns():
        assert pattern.revision
        assert pattern.equipment_kind
        assert pattern.intent
        assert pattern.required_signal_roles
        assert pattern.required_decisions
        assert pattern.invariants
        assert pattern.alarm_expectations
        assert pattern.recovery_expectations
        assert pattern.required_questions
        assert len(pattern.verification_scenarios) >= 3
        assert pattern.safety_boundary
        assert pattern.source_note
        assert pattern.trust == "curated_design_guidance"

        decision_ids = {decision.decision_id for decision in pattern.required_decisions}
        assert MANDATORY_BLOCKING_DECISION_IDS <= decision_ids
        assert MANDATORY_BLOCKING_DECISION_IDS <= set(pattern.blocking_decision_ids)
        assert all(decision.blocker_if_unresolved for decision in pattern.required_decisions)


def test_catalog_never_supplies_device_addresses_or_generic_timer_defaults() -> None:
    address_pattern = re.compile(r"(?<![A-Za-z0-9_])(?:X|Y|M|L|D|R|W|T|C)\d+\b")
    duration_pattern = re.compile(
        r"(?<![A-Za-z0-9_.])\d+(?:\.\d+)?\s*(?:ms|msec|s|sec|seconds?|minutes?)\b",
        re.IGNORECASE,
    )

    for pattern in iter_builtin_patterns():
        text = pattern_search_text(pattern)
        assert not address_pattern.search(text), pattern.pattern_id
        assert not duration_pattern.search(text), pattern.pattern_id
        assert "do not invent device addresses" in text
        assert "no generic time value is safe by default" in text


def test_vendor_and_safety_boundaries_are_explicit() -> None:
    for pattern in iter_builtin_patterns():
        boundary = pattern.safety_boundary
        assert "LS ELECTRIC" in boundary
        assert "Mitsubishi Electric" in boundary
        assert "Rockwell Automation" in boundary
        assert "neither generates nor claims a native" in boundary
        assert "cannot replace a safety PLC" in boundary
        assert "risk assessment" in boundary


def test_patterns_and_nested_contracts_are_immutable() -> None:
    pattern = next(iter_builtin_patterns())

    with pytest.raises(FrozenInstanceError):
        pattern.intent = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        pattern.required_signal_roles[0].description = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        pattern.required_decisions[0].prompt = "changed"  # type: ignore[misc]


def test_exports_are_json_serializable_detached_and_searchable() -> None:
    patterns = tuple(iter_builtin_patterns())
    documents = tuple(iter_builtin_knowledge_documents())

    assert len(documents) == len(patterns)
    for pattern, document in zip(patterns, documents, strict=True):
        assert document == pattern_to_knowledge_document(pattern)
        assert document["document_id"] == (
            f"builtin-control-pattern:{pattern.pattern_id}@{pattern.revision}"
        )
        assert document["kind"] == "control_pattern"
        assert document["vendor"] == "vendor-neutral"
        assert document["trust"] == "curated_design_guidance"
        assert pattern.pattern_id in str(document["content"])
        assert pattern.title in str(document["content"])
        assert json.loads(json.dumps(document, ensure_ascii=False)) == document

        exported = pattern_to_dict(pattern)
        assert isinstance(exported["required_signal_roles"], list)
        assert exported["pattern_id"] == pattern.pattern_id
        exported["intent"] = "mutated detached value"
        assert pattern.intent != exported["intent"]


def test_korean_search_terms_cover_common_equipment_requests() -> None:
    texts = {
        pattern.pattern_id: pattern_search_text(pattern) for pattern in iter_builtin_patterns()
    }

    assert "모터" in texts["motor.dol"]
    assert "실린더" in texts["cylinder.double_solenoid"]
    assert "인버터" in texts["drive.vfd"]
    assert "서보" in texts["axis.servo"]
    assert "알람" in texts["alarm.lifecycle"]
    assert "초기화" in texts["initialization_recovery"]
