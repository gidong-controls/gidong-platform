"""Digest-bound planning-to-engineering mission continuity projection."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .api_models import ApiModel, EngineeringConversationHandoffV1

_COMPLETED_STAGES = (
    "reviewed_plan",
    "native_build",
    "independent_reopen",
)
_REMAINING_STAGES = ("engineering_validation",)
_STATUS_KEYS = {
    "schemaVersion",
    "nativeProjectId",
    "objective",
    "completionCriteria",
    "reviewedPlanDigest",
    "buildBundleId",
    "buildReceiptDigest",
    "reopenReceiptDigest",
    "completedStages",
    "remainingStages",
    "reviewedProgram",
    "reviewedRequirements",
    "statusDigest",
}


def _canonical_digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class NativeEngineeringStatusV1(ApiModel):
    """The only native-build facts allowed to cross into the private Agent."""

    schema_version: Literal["1"]
    native_project_id: str = Field(min_length=1, max_length=256)
    objective: str = Field(min_length=1, max_length=4_000)
    completion_criteria: list[str] = Field(min_length=1, max_length=50)
    reviewed_plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_bundle_id: str = Field(min_length=1, max_length=256)
    build_receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reopen_receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    completed_stages: tuple[
        Literal["reviewed_plan"],
        Literal["native_build"],
        Literal["independent_reopen"],
    ]
    remaining_stages: tuple[Literal["engineering_validation"]]
    reviewed_program: dict[str, Any]
    reviewed_requirements: dict[str, str | None]
    status_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("objective")
    @classmethod
    def require_exact_objective(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("native engineering objective is not canonical")
        return value

    @field_validator("completion_criteria")
    @classmethod
    def require_exact_criteria(cls, value: list[str]) -> list[str]:
        if any(item != item.strip() or not item or len(item) > 4_000 for item in value):
            raise ValueError("native engineering completion criteria are not canonical")
        if len(value) != len(set(value)):
            raise ValueError("native engineering completion criteria must be unique")
        return value

    @model_validator(mode="after")
    def verify_digest(self) -> NativeEngineeringStatusV1:
        unsigned = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"status_digest"},
        )
        if not hmac.compare_digest(_canonical_digest(unsigned), self.status_digest):
            raise ValueError("native engineering status digest mismatched")
        return self

    @field_validator("reviewed_program")
    @classmethod
    def require_exact_reviewed_program(cls, value: dict[str, Any]) -> dict[str, Any]:
        required = {
            "programName",
            "language",
            "instructionCount",
            "rungCount",
            "source",
            "programCheckPassed",
            "independentReopenVerified",
        }
        if set(value) != required:
            raise ValueError("reviewed native program fields are not exact")
        source = value.get("source")
        if (
            not isinstance(value.get("programName"), str)
            or not value["programName"].strip()
            or value.get("language") != "LD"
            or not isinstance(source, list)
            or not source
            or len(source) > 20_000
            or any(not isinstance(line, str) or not line.strip() for line in source)
            or value.get("instructionCount") != len(source)
            or not isinstance(value.get("rungCount"), int)
            or isinstance(value.get("rungCount"), bool)
            or value["rungCount"] < 1
            or value.get("programCheckPassed") is not True
            or value.get("independentReopenVerified") is not True
        ):
            raise ValueError("reviewed native program is invalid")
        return value

    @field_validator("reviewed_requirements")
    @classmethod
    def require_exact_reviewed_requirements(
        cls,
        value: dict[str, str | None],
    ) -> dict[str, str | None]:
        required = {
            "machinePurpose",
            "sequence",
            "safetyAndInterlocks",
            "operatingModes",
            "performanceAndAcceptance",
        }
        if set(value) != required:
            raise ValueError("reviewed requirement fields are not exact")
        if not any(isinstance(item, str) and item.strip() for item in value.values()):
            raise ValueError("reviewed requirements are empty")
        for item in value.values():
            if item is not None and (
                not isinstance(item, str)
                or not item.strip()
                or item != item.strip()
                or len(item) > 20_000
            ):
                raise ValueError("reviewed requirement text is invalid")
        return value


def validate_native_engineering_status(value: Any) -> dict[str, Any]:
    """Validate an exact wire projection, including its self-binding digest."""

    if not isinstance(value, dict) or set(value) != _STATUS_KEYS:
        raise ValueError("native engineering status fields are not exact")
    return NativeEngineeringStatusV1.model_validate(value).model_dump(
        mode="json",
        by_alias=True,
    )


def derive_native_engineering_status(
    *,
    handoff: dict[str, Any],
    reviewed_plan: dict[str, Any],
    project_brief: dict[str, Any],
    native_project_result: dict[str, Any],
) -> dict[str, Any]:
    """Project reviewed intent and terminal native stages without raw receipts."""

    provenance = EngineeringConversationHandoffV1.model_validate(handoff)
    if reviewed_plan.get("schemaVersion") != "2":
        raise ValueError("reviewed project plan schema is invalid")
    objective_value = reviewed_plan.get("objective")
    if (
        not isinstance(objective_value, str)
        or not objective_value
        or objective_value != objective_value.strip()
    ):
        raise ValueError("reviewed project plan objective is missing")
    objective = objective_value

    criteria: list[str] = []
    seen: set[str] = set()

    def append_criterion(value: Any) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        criterion = value.strip()
        if criterion not in seen:
            seen.add(criterion)
            criteria.append(criterion)

    sections = reviewed_plan.get("sections")
    if not isinstance(sections, list):
        raise TypeError("reviewed project plan sections are invalid")
    for section in sections:
        if not isinstance(section, dict) or not isinstance(section.get("items"), list):
            raise TypeError("reviewed project plan section is invalid")
        for item in section["items"]:
            if not isinstance(item, dict):
                raise TypeError("reviewed project plan item is invalid")
            append_criterion(item.get("verificationMethod"))

    acceptance = project_brief.get("performance_and_acceptance")
    if acceptance is not None:
        if not isinstance(acceptance, str):
            raise TypeError("project brief performance_and_acceptance is invalid")
        append_criterion(acceptance)
    if not criteria:
        raise ValueError("native engineering completion criteria are empty")

    reopen = native_project_result.get("reopenVerification")
    receipt = reopen.get("receipt") if isinstance(reopen, dict) else None
    program_readback = (
        receipt.get("programReadback") if isinstance(receipt, dict) else None
    )
    if (
        not isinstance(reopen, dict)
        or reopen.get("state") != "verified"
        or not isinstance(receipt, dict)
        or receipt.get("status") != "REOPEN_VERIFIED"
        or not isinstance(program_readback, dict)
    ):
        raise ValueError("verified native program handoff is unavailable")
    source = program_readback.get("exactInstructionReadback")
    reviewed_program = {
        "programName": program_readback.get("programName"),
        "language": program_readback.get("language"),
        "instructionCount": len(source) if isinstance(source, list) else None,
        "rungCount": program_readback.get("rungCount"),
        "source": source,
        "programCheckPassed": program_readback.get("programCheckPassed"),
        "independentReopenVerified": True,
    }
    def reviewed_text(key: str) -> str | None:
        value = project_brief.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError(f"project brief {key} is invalid")
        canonical = value.strip()
        return canonical or None

    requirement_quotes: dict[str, list[str]] = {}
    requirement_sources = reviewed_plan.get("requirementSources", [])
    if not isinstance(requirement_sources, list):
        raise TypeError("reviewed project plan requirement sources are invalid")
    for source_item in requirement_sources:
        if not isinstance(source_item, dict):
            raise TypeError("reviewed project plan requirement source is invalid")
        field = source_item.get("field")
        quote = source_item.get("quote")
        if not isinstance(field, str) or not isinstance(quote, str):
            raise TypeError("reviewed project plan requirement source is invalid")
        canonical_quote = quote.strip()
        if canonical_quote and canonical_quote not in requirement_quotes.setdefault(
            field, []
        ):
            requirement_quotes[field].append(canonical_quote)

    def reviewed_requirement(field: str, brief_key: str) -> str | None:
        quotes = requirement_quotes.get(field, [])
        if quotes:
            return "\n\n".join(quotes)
        return reviewed_text(brief_key)

    reviewed_requirements = {
        "machinePurpose": reviewed_text("machine_purpose"),
        "sequence": reviewed_requirement("sequence", "sequence"),
        "safetyAndInterlocks": reviewed_requirement(
            "safety_and_interlocks", "safety_and_interlocks"
        ),
        "operatingModes": reviewed_requirement(
            "operating_modes", "operating_modes"
        ),
        "performanceAndAcceptance": reviewed_requirement(
            "performance_and_acceptance", "performance_and_acceptance"
        ),
    }

    unsigned = {
        "schemaVersion": "1",
        "nativeProjectId": provenance.native_project_id,
        "objective": objective,
        "completionCriteria": criteria,
        "reviewedPlanDigest": provenance.reviewed_plan_digest,
        "buildBundleId": provenance.build_bundle_id,
        "buildReceiptDigest": provenance.build_receipt_digest,
        "reopenReceiptDigest": provenance.reopen_receipt_digest,
        "completedStages": list(_COMPLETED_STAGES),
        "remainingStages": list(_REMAINING_STAGES),
        "reviewedProgram": reviewed_program,
        "reviewedRequirements": reviewed_requirements,
    }
    return validate_native_engineering_status(
        {**unsigned, "statusDigest": _canonical_digest(unsigned)}
    )
