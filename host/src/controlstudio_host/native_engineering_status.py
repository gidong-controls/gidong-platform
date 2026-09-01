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
    "projectSha256",
    "completedStages",
    "remainingStages",
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
    project_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    completed_stages: tuple[
        Literal["reviewed_plan"],
        Literal["native_build"],
        Literal["independent_reopen"],
    ]
    remaining_stages: tuple[Literal["engineering_validation"]]
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
    if not isinstance(acceptance, str) or not acceptance.strip():
        raise ValueError("project brief performance_and_acceptance is missing")
    append_criterion(acceptance)
    if not criteria:
        raise ValueError("native engineering completion criteria are empty")

    unsigned = {
        "schemaVersion": "1",
        "nativeProjectId": provenance.native_project_id,
        "objective": objective,
        "completionCriteria": criteria,
        "reviewedPlanDigest": provenance.reviewed_plan_digest,
        "buildBundleId": provenance.build_bundle_id,
        "buildReceiptDigest": provenance.build_receipt_digest,
        "reopenReceiptDigest": provenance.reopen_receipt_digest,
        "projectSha256": provenance.project_sha256,
        "completedStages": list(_COMPLETED_STAGES),
        "remainingStages": list(_REMAINING_STAGES),
    }
    return validate_native_engineering_status(
        {**unsigned, "statusDigest": _canonical_digest(unsigned)}
    )
