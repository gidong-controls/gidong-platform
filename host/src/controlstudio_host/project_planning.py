from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .api_models import (
    ProjectBriefV1,
    ProjectPlanArtifactV1,
    ProjectPlanAttachmentV1,
    ProjectRequirementSourceV1,
)
from .models import Message


@dataclass(frozen=True)
class ProjectPlanSnapshot:
    message_id: str
    plan_digest: str
    artifact: dict[str, Any]


def calculate_project_plan_digest(artifact: dict[str, Any]) -> str:
    return _canonical_digest(artifact)


def _canonical_digest(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def calculate_project_brief_digest(brief: dict[str, Any] | ProjectBriefV1) -> str:
    """Bind review authority to the Host-owned, typed project brief."""

    validated = (
        brief if isinstance(brief, ProjectBriefV1) else ProjectBriefV1.model_validate(brief)
    )
    payload = validated.model_dump(mode="json", by_alias=True)
    # Adding an unused optional intake field must not invalidate legacy reviews.
    if not validated.supplemental_requirements:
        payload.pop("supplementalRequirements", None)
    return _canonical_digest(payload)


def append_planning_clarification(brief: dict[str, Any], content: str) -> dict[str, Any]:
    """Keep a user's follow-up verbatim without guessing which fields it resolves."""

    validated = ProjectBriefV1.model_validate(brief)
    if not content.strip():
        return validated.model_dump(mode="python")
    previous_parts: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"\n\s*\n", validated.supplemental_requirements):
        normalized = part.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        previous_parts.append(normalized)
    normalized_content = content.strip()
    payload = validated.model_dump(mode="python")
    if normalized_content not in seen:
        previous_parts.append(content)
    payload["supplemental_requirements"] = "\n\n".join(previous_parts)
    return ProjectBriefV1.model_validate(payload).model_dump(mode="python")


def calculate_project_plan_attachment_revision_digest(
    attachments: Sequence[dict[str, Any] | ProjectPlanAttachmentV1],
) -> str:
    """Bind source ids to their current immutable content and revision values."""

    revisions: list[dict[str, str]] = []
    source_ids: set[str] = set()
    for attachment in attachments:
        validated = (
            attachment
            if isinstance(attachment, ProjectPlanAttachmentV1)
            else ProjectPlanAttachmentV1.model_validate(attachment)
        )
        if validated.source_id in source_ids:
            raise ValueError("project plan attachments contain duplicate source ids")
        source_ids.add(validated.source_id)
        revisions.append(
            {
                "sourceId": validated.source_id,
                "sha256": validated.sha256,
                "revision": validated.revision,
            }
        )
    revisions.sort(key=lambda item: item["sourceId"])
    return _canonical_digest(
        {
            "schemaVersion": "controlstudio-project-source-revisions/v1",
            "sources": revisions,
        }
    )


def _pointer_segment(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("native design review identity is missing")
    return value.replace("~", "~0").replace("/", "~1")


def _semantic_text(path: str, value: Any) -> str:
    return json.dumps(
        {"path": path, "value": value},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _condition_value(condition: Any) -> dict[str, str]:
    if not isinstance(condition, dict):
        raise ValueError("native design condition is invalid")
    signal_id = condition.get("signalId")
    state = condition.get("state")
    if not isinstance(signal_id, str) or state not in {"on", "off"}:
        raise ValueError("native design condition identity is invalid")
    return {"signalId": signal_id, "state": state}


def _condition_suffix(condition: Any) -> str:
    value = _condition_value(condition)
    return f"{_pointer_segment(value['signalId'])}:{value['state']}"


_SEMANTIC_SECTION_BY_COLLECTION = {
    "ioPoints": "native-io-map",
    "operatingModes": "native-mode-model",
    "controlNetworks": "native-control-networks",
    "timers": "native-timers",
    "sequences": "native-sequences",
    "alarms": "native-alarms",
    "recoveryRules": "native-recovery",
    "safetyRules": "native-safety-rules",
}

_COLLECTION_ID_FIELDS = {
    "ioPoints": "signalId",
    "operatingModes": "modeId",
    "controlNetworks": "networkId",
    "timers": "timerId",
    "sequences": "sequenceId",
    "alarms": "alarmId",
    "recoveryRules": "recoveryId",
    "safetyRules": "ruleId",
}

_TECHNICAL_SECTION_ID = "native-technical-configuration"
_SIMULATION_PROFILE_SECTION_ID = "native-simulation-profile"
_LADDER_SECTION_ID = "native-ladder-rungs"
_NATIVE_SYMBOL_SECTION_ID = "native-symbol-metadata"
_SEMANTIC_SECTION_ORDER = (
    "user-requirements",
    _TECHNICAL_SECTION_ID,
    _SIMULATION_PROFILE_SECTION_ID,
    "native-io-map",
    _NATIVE_SYMBOL_SECTION_ID,
    "native-mode-model",
    "native-control-networks",
    _LADDER_SECTION_ID,
    "native-timers",
    "native-sequences",
    "native-alarms",
    "native-recovery",
    "native-safety-rules",
)

_COLLECTION_EVIDENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])[ \t]+|[\r\n]+")

_COLLECTION_AUTHORITY_FIELDS = {
    "ioPoints": ("hardware_and_io",),
    "operatingModes": ("operating_modes",),
    "controlNetworks": (
        "hardware_and_io",
        "sequence",
        "safety_and_interlocks",
    ),
    "timers": ("sequence", "performance_and_acceptance"),
    "sequences": ("sequence",),
    "alarms": ("sequence", "safety_and_interlocks"),
    "recoveryRules": ("sequence", "safety_and_interlocks"),
    "safetyRules": ("safety_and_interlocks",),
}

_GENERIC_SCOPE_ACKNOWLEDGEMENTS = {
    "ok",
    "okay",
    "yes",
    "좋아",
    "좋습니다",
    "네",
    "예",
    "동의",
    "확인",
    "맞아",
    "맞습니다",
}


def _collection_evidence_clauses(values: Sequence[str]) -> list[str]:
    """Return exact full fields plus punctuation-preserving sentences or lines."""

    clauses: list[str] = []
    for value in values:
        full_field = value.strip()
        if full_field and full_field not in clauses:
            clauses.append(full_field)
        for clause in _COLLECTION_EVIDENCE_SPLIT_RE.split(value):
            normalized = clause.strip()
            if normalized and normalized not in clauses:
                clauses.append(normalized)
    return clauses


_REQUIREMENT_UNRESOLVED = re.compile(
    r"(?:\bunknown\b|not\s+(?:yet\s+)?confirmed|to\s+be\s+confirmed|"
    r"\btbd\b|\btbc\b|\bpending\b|\btentative\b|\bprovisional\b|"
    r"\bassumed?\b|\bdraft\b|"
    r"미정|미확정|확인되지|아직\s+모|모름|검토\s*(?:필요|중)|"
    r"확인\s*필요|예정|임시|가정|초안)", re.IGNORECASE,
)


class _RequirementSourceValidationError(ValueError):
    """Closed citation failure metadata; never retain a submitted value."""

    def __init__(self, source_index: int, source_field: str, reason_code: str) -> None:
        reason_fields = {
            "duplicate_field": "field", "message_not_available": "message_id",
            "quote_not_exact": "quote", "generic_ack": "quote", "unresolved_quote": "quote",
        }
        if (
            type(source_index) is not int or source_index < 0
            or not isinstance(reason_code, str)
            or source_field != reason_fields.get(reason_code)
            or reason_code not in reason_fields
        ):
            raise ValueError("requirement citation failure metadata is invalid")
        super().__init__("requirementSources must cite unique fields and exact resolved user quotes")
        self.source_index = source_index
        self.source_field = source_field
        self.reason_code = reason_code


def validate_requirement_sources(
    brief: ProjectBriefV1, sources: Sequence[ProjectRequirementSourceV1],
    *, require_complete: bool, requirement_messages: dict[str, str] | None = None,
) -> dict[str, str]:
    fields = ("sequence", "hardware_and_io", "safety_and_interlocks",
              "operating_modes", "performance_and_acceptance")
    corpus = set(_collection_evidence_clauses([
        getattr(brief, field) for field in (*fields, "machine_purpose", "supplemental_requirements")
    ]))
    mapped: dict[str, str] = {}
    for index, source in enumerate(sources):
        source_corpus = corpus
        message_available = True
        if source.message_id is not None:
            message = (requirement_messages or {}).get(source.message_id)
            message_available = isinstance(message, str)
            source_corpus = (
                set(_collection_evidence_clauses([message]))
                if isinstance(message, str)
                else set()
            )
        if source.field in mapped:
            raise _RequirementSourceValidationError(index, "field", "duplicate_field")
        if source.quote not in source_corpus:
            if not message_available:
                raise _RequirementSourceValidationError(index, "message_id", "message_not_available")
            raise _RequirementSourceValidationError(index, "quote", "quote_not_exact")
        if source.quote.casefold() in {"ok", "okay", "yes", "네", "예", "확인", "동의"}:
            raise _RequirementSourceValidationError(index, "quote", "generic_ack")
        if _REQUIREMENT_UNRESOLVED.search(source.quote):
            raise _RequirementSourceValidationError(index, "quote", "unresolved_quote")
        mapped[source.field] = source.quote
    if require_complete and any(
        not getattr(brief, field).strip() or _REQUIREMENT_UNRESOLVED.search(getattr(brief, field))
        for field in ("project_name", "machine_purpose")
    ):
        raise ValueError("ready plan requires a resolved project name and purpose")
    if require_complete and any(
        field not in mapped and (not getattr(brief, field).strip()
                                or _REQUIREMENT_UNRESOLVED.search(getattr(brief, field)))
        for field in fields
    ):
        raise ValueError("ready plan requires source coverage for unresolved brief fields")
    return mapped


def validate_project_plan_scope_evidence(
    artifact: dict[str, Any],
    project_brief: dict[str, Any] | ProjectBriefV1,
    *,
    requirement_messages: dict[str, str] | None = None,
) -> None:
    """Require scope citations to be exact current brief fields, sentences, or lines."""

    validated_artifact = ProjectPlanArtifactV1.model_validate(artifact)
    brief = (
        project_brief
        if isinstance(project_brief, ProjectBriefV1)
        else ProjectBriefV1.model_validate(project_brief)
    )
    scope = validated_artifact.design.collection_scope
    if scope is None:
        raise ValueError("native design requires explicit collectionScope")
    brief_values = brief.model_dump(mode="json")
    sources = validate_requirement_sources(
        brief, validated_artifact.requirement_sources,
        requirement_messages=requirement_messages,
        require_complete=(validated_artifact.status == "ready_for_review" and bool(
            brief.supplemental_requirements or validated_artifact.requirement_sources
        )),
    )
    for collection, authority_fields in _COLLECTION_AUTHORITY_FIELDS.items():
        field_name = {
            "ioPoints": "io_points",
            "operatingModes": "operating_modes",
            "controlNetworks": "control_networks",
            "timers": "timers",
            "sequences": "sequences",
            "alarms": "alarms",
            "recoveryRules": "recovery_rules",
            "safetyRules": "safety_rules",
        }[collection]
        decision = getattr(scope, field_name)
        authority_clauses = set(
            _collection_evidence_clauses(
                [str(brief_values.get(name) or "") for name in authority_fields]
                + [sources.get(name, "") for name in authority_fields]
                + [brief.supplemental_requirements]
            )
        )
        if any(
            evidence.casefold() in _GENERIC_SCOPE_ACKNOWLEDGEMENTS
            or evidence not in authority_clauses
            for evidence in decision.evidence
        ):
            raise ValueError(
                f"collectionScope.{collection} evidence is not an exact current brief "
                "field, sentence, or line"
            )


def expected_project_plan_semantic_items(
    artifact: dict[str, Any],
) -> list[dict[str, str]]:
    """Derive every executable review row from the canonical native design."""

    validated = ProjectPlanArtifactV1.model_validate(artifact)
    canonical = validated.model_dump(mode="json", by_alias=True)
    design = canonical["design"]
    technical = canonical.get("technicalConfiguration")
    if validated.status == "ready_for_review" and not isinstance(technical, dict):
        raise ValueError("ready project plan requires reviewed technical configuration")
    scope = design.get("collectionScope")
    if not isinstance(scope, dict):
        raise ValueError("native design requires explicit collectionScope")
    ladder_rungs = design.get("ladderRungs")
    if not isinstance(ladder_rungs, list):
        raise ValueError("native design ladderRungs are invalid")
    if validated.status == "ready_for_review" and not ladder_rungs:
        raise ValueError(
            "ready project plan requires at least one explicit ladder rung"
        )
    rows: list[dict[str, Any]] = []

    def add(section_id: str, path: str, value: Any) -> None:
        rows.append(
            {
                "sectionId": section_id,
                "itemId": path,
                "text": _semantic_text(path, value),
            }
        )

    for source in canonical.get("requirementSources", []):
        message_id = source.get("messageId")
        if isinstance(message_id, str):
            add(
                "user-requirements",
                f"requirement/message/{_pointer_segment(message_id)}/{_pointer_segment(source['field'])}",
                {
                    "field": source["field"],
                    "messageId": message_id,
                    "quote": source["quote"],
                },
            )

    if isinstance(technical, dict):
        for field_name, value in (
            ("projectName", canonical["projectName"]),
            ("plcSeries", technical["plcSeries"]),
            ("cpuModel", technical["cpuModel"]),
            ("programName", technical["programName"]),
            ("language", technical["language"]),
            ("description", technical["description"]),
        ):
            add(_TECHNICAL_SECTION_ID, f"technical/{field_name}", value)

    scenario = design.get("simulationScenario")
    if isinstance(scenario, dict):
        for index, step in enumerate(scenario["steps"]):
            add(
                _SIMULATION_PROFILE_SECTION_ID,
                f"design/simulationScenario/steps/{index}",
                step,
            )

    profile = design.get("indexAcceptanceBindings")
    if isinstance(profile, dict):
        io_by_id = {item["signalId"]: item for item in design["ioPoints"]}
        mode_by_id = {item["modeId"]: item for item in design["operatingModes"]}
        alarm_by_id = {item["alarmId"]: item for item in design["alarms"]}
        timer_by_id = {item["timerId"]: item for item in design["timers"]}
        for role, signal_id in profile["signals"].items():
            signal = io_by_id.get(signal_id, {})
            add(
                _SIMULATION_PROFILE_SECTION_ID,
                f"design/indexAcceptanceBindings/signals/{_pointer_segment(role)}",
                {
                    "role": role,
                    "signalId": signal_id,
                    "signalName": signal.get("description", signal_id),
                    "signalSymbol": signal.get("symbol", signal_id),
                },
            )
        for role, mode_id in profile["modes"].items():
            mode = mode_by_id.get(mode_id, {})
            add(
                _SIMULATION_PROFILE_SECTION_ID,
                f"design/indexAcceptanceBindings/modes/{_pointer_segment(role)}",
                {"role": role, "modeId": mode_id, "modeName": mode.get("name", mode_id)},
            )
        for role, alarm_id in profile["alarms"].items():
            alarm = alarm_by_id.get(alarm_id, {})
            add(
                _SIMULATION_PROFILE_SECTION_ID,
                f"design/indexAcceptanceBindings/alarms/{_pointer_segment(role)}",
                {
                    "role": role,
                    "alarmId": alarm_id,
                    "alarmName": alarm.get("name", alarm_id),
                },
            )
        timer_id = profile["timerId"]
        timer = timer_by_id.get(timer_id, {})
        add(
            _SIMULATION_PROFILE_SECTION_ID,
            "design/indexAcceptanceBindings/timer",
            {"timerId": timer_id, "timerName": timer.get("name", timer_id)},
        )

    for collection, section_id in _SEMANTIC_SECTION_BY_COLLECTION.items():
        values = design[collection]
        ids = [item[_COLLECTION_ID_FIELDS[collection]] for item in values]
        decision = scope[collection]
        add(
            section_id,
            f"scope/{collection}",
            {
                "status": decision["status"],
                "reason": decision["reason"],
                "evidence": decision["evidence"],
                "basis": decision["basis"],
                "count": len(ids),
                "ids": ids,
            },
        )

    for point in design["ioPoints"]:
        base = f"design/ioPoints/{_pointer_segment(point['signalId'])}"
        add(
            "native-io-map",
            f"{base}/mapping",
            {
                "symbol": point["symbol"],
                "address": point["address"],
                "kind": point["kind"],
            },
        )
        add("native-io-map", f"{base}/normalState", point["normalState"])
        add("native-io-map", f"{base}/safeState", point["safeState"])

    for position, metadata in enumerate(design.get("nativeSymbols") or []):
        add(
            _NATIVE_SYMBOL_SECTION_ID,
            f"design/nativeSymbols/{position:04d}",
            metadata,
        )

    for mode in design["operatingModes"]:
        base = f"design/operatingModes/{_pointer_segment(mode['modeId'])}"
        add("native-mode-model", f"{base}/activation", mode["activation"])
        add("native-mode-model", f"{base}/transitions", mode["allowedTransitions"])
        for target in mode["allowedTransitions"]:
            add(
                "native-mode-model",
                f"{base}/transition/{_pointer_segment(target)}",
                target,
            )

    for network in design["controlNetworks"]:
        base = f"design/controlNetworks/{_pointer_segment(network['networkId'])}"
        add("native-control-networks", f"{base}/output", network["outputSignalId"])
        for condition in network["permitAll"]:
            add(
                "native-control-networks",
                f"{base}/permit/{_condition_suffix(condition)}",
                _condition_value(condition),
            )
        add("native-control-networks", f"{base}/mode", network["modeId"])
        add("native-control-networks", f"{base}/step", network["stepId"])

    rung_ids = [rung["rungId"] for rung in ladder_rungs]
    add(
        _LADDER_SECTION_ID,
        "scope/ladderRungs",
        {
            "status": "confirmed",
            "count": len(rung_ids),
            "ids": rung_ids,
        },
    )
    for position, rung in enumerate(ladder_rungs):
        base = f"design/ladderRungs/{_pointer_segment(rung['rungId'])}"
        add(_LADDER_SECTION_ID, f"{base}/position", position)
        add(_LADDER_SECTION_ID, f"{base}/description", rung["description"])
        add(_LADDER_SECTION_ID, f"{base}/condition", rung["condition"])
        add(_LADDER_SECTION_ID, f"{base}/action", rung["action"])

    for timer in design["timers"]:
        base = f"design/timers/{_pointer_segment(timer['timerId'])}"
        add(
            "native-timers",
            f"{base}/address",
            {"address": timer["address"], "instruction": timer["instruction"]},
        )
        add(
            "native-timers",
            f"{base}/duration",
            {
                "timeBaseMs": timer["timeBaseMs"],
                "presetTicks": timer["presetTicks"],
                "durationMs": timer["durationMs"],
            },
        )
        for condition in timer["enableAll"]:
            add(
                "native-timers",
                f"{base}/enable/{_condition_suffix(condition)}",
                _condition_value(condition),
            )
        add("native-timers", f"{base}/mode", timer["modeId"])
        add("native-timers", f"{base}/step", timer["stepId"])

    for sequence in design["sequences"]:
        base = f"design/sequences/{_pointer_segment(sequence['sequenceId'])}"
        add("native-sequences", f"{base}/restartPolicy", sequence["restartPolicy"])
        for relation, conditions in (
            ("start", sequence["startAll"]),
            ("reset", sequence["resetAll"]),
        ):
            for condition in conditions:
                add(
                    "native-sequences",
                    f"{base}/{relation}/{_condition_suffix(condition)}",
                    _condition_value(condition),
                )
        add("native-sequences", f"{base}/mode", sequence["modeId"])
        for position, step in enumerate(sequence["steps"]):
            step_base = f"{base}/step/{_pointer_segment(step['stepId'])}"
            add(
                "native-sequences",
                f"{step_base}/address",
                {"address": step["address"], "position": position},
            )
            for condition in step["completeAll"]:
                add(
                    "native-sequences",
                    f"{step_base}/complete/{_condition_suffix(condition)}",
                    _condition_value(condition),
                )

    for alarm in design["alarms"]:
        base = f"design/alarms/{_pointer_segment(alarm['alarmId'])}"
        add("native-alarms", f"{base}/latch", alarm["latchSignalId"])
        for condition in alarm["triggerAll"]:
            add(
                "native-alarms",
                f"{base}/trigger/{_condition_suffix(condition)}",
                _condition_value(condition),
            )
        for output in alarm["affectedOutputSignalIds"]:
            add(
                "native-alarms",
                f"{base}/output/{_pointer_segment(output)}",
                output,
            )
        add("native-alarms", f"{base}/mode", alarm["modeId"])
        add("native-alarms", f"{base}/step", alarm["stepId"])

    for recovery in design["recoveryRules"]:
        base = f"design/recoveryRules/{_pointer_segment(recovery['recoveryId'])}"
        for condition in recovery["whenAll"]:
            add(
                "native-recovery",
                f"{base}/when/{_condition_suffix(condition)}",
                _condition_value(condition),
            )
        add("native-recovery", f"{base}/alarms", recovery["resetAlarmIds"])
        for alarm_id in recovery["resetAlarmIds"]:
            add(
                "native-recovery",
                f"{base}/alarm/{_pointer_segment(alarm_id)}",
                alarm_id,
            )
        add("native-recovery", f"{base}/sequences", recovery["resetSequenceIds"])
        for sequence_id in recovery["resetSequenceIds"]:
            add(
                "native-recovery",
                f"{base}/sequence/{_pointer_segment(sequence_id)}",
                sequence_id,
            )

    for rule in design["safetyRules"]:
        base = f"design/safetyRules/{_pointer_segment(rule['ruleId'])}"
        add("native-safety-rules", f"{base}/classification", rule["classification"])
        add("native-safety-rules", f"{base}/action", rule["action"])
        permit = rule["permitWhen"]
        add("native-safety-rules", f"{base}/permit", permit)
        if permit is not None:
            add(
                "native-safety-rules",
                f"{base}/permit/{_condition_suffix(permit)}",
                _condition_value(permit),
            )
        for output in rule["affectedOutputSignalIds"]:
            add(
                "native-safety-rules",
                f"{base}/output/{_pointer_segment(output)}",
                output,
            )

    item_ids = [str(row["itemId"]) for row in rows]
    if len(set(item_ids)) != len(item_ids):
        raise ValueError("native design semantic review paths contain a collision")
    by_section: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_section.setdefault(str(row["sectionId"]), []).append(row)
    return [
        row
        for section_id in _SEMANTIC_SECTION_ORDER
        for row in sorted(by_section.get(section_id, []), key=lambda item: item["itemId"])
    ]


def project_plan_review_item_ids(artifact: dict[str, Any]) -> list[str]:
    """Validate semantic rows and return the exact ordered review universe."""

    expected_rows = expected_project_plan_semantic_items(artifact)
    expected_by_id = {row["itemId"]: row for row in expected_rows}
    sections = artifact.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("project plan must contain reviewable sections")
    actual_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    item_ids: list[str] = []
    for section in sections:
        if not isinstance(section, dict) or not isinstance(section.get("sectionId"), str):
            raise ValueError("project plan contains an invalid section")
        section_id = section["sectionId"]
        items = section.get("items")
        if not isinstance(items, list):
            raise ValueError("project plan section items are invalid")
        for item in items:
            item_id = item.get("itemId") if isinstance(item, dict) else None
            if not isinstance(item_id, str) or not item_id:
                raise ValueError("project plan contains an invalid item identity")
            if item_id in actual_by_id:
                raise ValueError("project plan contains duplicate or colliding item identities")
            actual_by_id[item_id] = (section_id, item)
            item_ids.append(item_id)
    for item_id, expected in expected_by_id.items():
        actual = actual_by_id.get(item_id)
        if actual is None:
            raise ValueError(f"project plan is missing semantic review item {item_id}")
        section_id, item = actual
        if (
            section_id != expected["sectionId"]
            or item.get("text") != expected["text"]
            or item.get("source") != "agent"
            or item.get("status") != "proposed"
            or item.get("blocking") is not False
        ):
            raise ValueError(f"semantic review item {item_id} does not match native design")
    unknown_semantic_ids = {
        item_id
        for item_id in actual_by_id
        if item_id.startswith(("requirement/", "technical/", "scope/", "design/"))
        and item_id not in expected_by_id
    }
    if unknown_semantic_ids:
        raise ValueError("project plan contains unknown semantic review identities")
    if not item_ids:
        raise ValueError("project plan review item universe is empty")
    return item_ids


def validate_project_plan_artifact(
    artifact: Any,
    plan_digest: Any,
    *,
    require_current_ready_contract: bool = False,
) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        raise TypeError("project plan artifact is missing")
    if (
        not isinstance(plan_digest, str)
        or len(plan_digest) != 64
        or any(character not in "0123456789abcdef" for character in plan_digest)
    ):
        raise ValueError("project plan digest is not lowercase SHA-256")
    try:
        validated = ProjectPlanArtifactV1.model_validate(artifact)
    except ValidationError as exc:
        raise ValueError("project plan artifact does not match the public contract") from exc
    if (
        require_current_ready_contract
        and validated.status == "ready_for_review"
        and not validated.design.ladder_rungs
    ):
        raise ValueError(
            "ready project plan requires at least one explicit ladder rung"
        )
    if calculate_project_plan_digest(artifact) != plan_digest:
        raise ValueError("project plan digest does not match its artifact")
    return artifact


def latest_project_plan_snapshot(
    messages: Sequence[Message],
) -> ProjectPlanSnapshot | None:
    for message in reversed(messages):
        artifact = message.metadata.get("plan_artifact")
        plan_digest = message.metadata.get("plan_digest")
        if artifact is None and plan_digest is None:
            continue
        try:
            validated = validate_project_plan_artifact(artifact, plan_digest)
        except (TypeError, ValueError):
            continue
        assert isinstance(plan_digest, str)
        return ProjectPlanSnapshot(
            message_id=message.id,
            plan_digest=plan_digest,
            artifact=validated,
        )
    return None


def _section_map(artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(section["sectionId"]): section
        for section in artifact.get("sections", [])
        if isinstance(section, dict) and isinstance(section.get("sectionId"), str)
    }


def _revision_entries(artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for section in artifact.get("sections", []):
        if not isinstance(section, dict):
            continue
        section_id = section.get("sectionId")
        section_title = section.get("title")
        if not isinstance(section_id, str) or not isinstance(section_title, str):
            continue
        for item in section.get("items", []):
            if not isinstance(item, dict) or not isinstance(item.get("itemId"), str):
                continue
            item_id = str(item["itemId"])
            entries[item_id] = {
                "itemId": item_id,
                "kind": "plan_item",
                "sectionId": section_id,
                "sectionTitle": section_title,
                "text": str(item.get("text") or ""),
                "status": str(item.get("status") or ""),
                "source": str(item.get("source") or ""),
            }
    for decision in artifact.get("openDecisions", []):
        if not isinstance(decision, dict) or not isinstance(
            decision.get("decisionId"), str
        ):
            continue
        item_id = str(decision["decisionId"])
        entries[item_id] = {
            "itemId": item_id,
            "kind": "open_decision",
            "sectionId": "open-decisions",
            "sectionTitle": "열린 결정",
            "text": str(decision.get("question") or ""),
            "status": str(decision.get("status") or ""),
            "source": str(decision.get("source") or ""),
        }
    return entries


def build_project_plan_revision(
    *,
    previous: ProjectPlanSnapshot,
    current_plan_digest: str,
    current_artifact: dict[str, Any],
) -> dict[str, Any]:
    validate_project_plan_artifact(current_artifact, current_plan_digest)
    previous_entries = _revision_entries(previous.artifact)
    current_entries = _revision_entries(current_artifact)
    previous_sections = _section_map(previous.artifact)
    current_sections = _section_map(current_artifact)

    added_ids = current_entries.keys() - previous_entries.keys()
    removed_ids = previous_entries.keys() - current_entries.keys()
    added_items = sorted(
        (current_entries[item_id] for item_id in added_ids),
        key=lambda item: (item["sectionId"], item["text"], item["itemId"]),
    )
    removed_items = sorted(
        (previous_entries[item_id] for item_id in removed_ids),
        key=lambda item: (item["sectionId"], item["text"], item["itemId"]),
    )

    section_order = dict.fromkeys(
        [*current_sections.keys(), *previous_sections.keys(), "open-decisions"]
    )
    changed_section_ids = [
        section_id
        for section_id in section_order
        if (
            section_id == "open-decisions"
            and previous.artifact.get("openDecisions")
            != current_artifact.get("openDecisions")
        )
        or (
            section_id != "open-decisions"
            and previous_sections.get(section_id) != current_sections.get(section_id)
        )
    ]
    tracked_fields = (
        "projectName",
        "status",
        "summary",
        "objective",
        "attachments",
        "technicalConfiguration",
        "design",
        "sections",
        "openDecisions",
        "completeness",
    )
    changed_fields = [
        field
        for field in tracked_fields
        if previous.artifact.get(field) != current_artifact.get(field)
    ]
    return {
        "schemaVersion": "1",
        "previousMessageId": previous.message_id,
        "previousPlanDigest": previous.plan_digest,
        "currentPlanDigest": current_plan_digest,
        "changedFields": changed_fields,
        "changedSectionIds": changed_section_ids,
        "addedItems": added_items,
        "removedItems": removed_items,
    }
