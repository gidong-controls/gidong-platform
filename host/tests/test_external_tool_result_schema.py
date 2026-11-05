"""The published wire schema accepts the actual sanitized failure envelope."""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from controlstudio_contracts.v1 import ExternalToolResultV1


SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "schemas/agent/v1/external-tool-result.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)
FAILURE = {"schema": "controlstudio-native-failure/v1", "kind": "INPUT_UNKNOWN",
           "message": "The native outcome is unknown.", "details": {"input_state_unknown": True}}


@pytest.mark.parametrize("code", [None, "NATIVE_UNKNOWN"])
def test_actual_native_failure_wire_serialization_is_valid(code):
    failure = {**FAILURE, **({"code": code} if code else {})}
    model = ExternalToolResultV1(request_id="request", tool_name="editor",
        status="failed", failure_kind="tool_error", nativeFailure=failure)
    VALIDATOR.validate(model.model_dump(mode="json", by_alias=True))


@pytest.mark.parametrize("status,kind", [("completed", None), ("failed", "cancelled"),
                                        ("failed", "transport"), ("failed", "host_policy")])
def test_native_failure_cannot_be_used_for_other_outcomes(status, kind):
    wire = {"protocol_version": "1.0", "request_id": "request", "tool_name": "editor",
            "status": status, "failure_kind": kind, "nativeFailure": copy.deepcopy(FAILURE)}
    assert not VALIDATOR.is_valid(wire)
    with pytest.raises(ValidationError):
        ExternalToolResultV1.model_validate(wire)
    wire.pop("nativeFailure")
    VALIDATOR.validate(wire)


def test_completed_serialization_omits_absent_failure():
    wire = ExternalToolResultV1(request_id="request", tool_name="editor",
                               status="completed").model_dump(mode="json", by_alias=True)
    assert "nativeFailure" not in wire
    VALIDATOR.validate(wire)


@pytest.mark.parametrize("change", [{"code": ""}, {"schema": "unknown"}, {"extra": True}])
def test_failure_shape_remains_strict(change):
    wire = {"protocol_version": "1.0", "request_id": "request", "tool_name": "editor",
            "status": "failed", "failure_kind": "tool_error", "nativeFailure": {**FAILURE, **change}}
    assert not VALIDATOR.is_valid(wire)
