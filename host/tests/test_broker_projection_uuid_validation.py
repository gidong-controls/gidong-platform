"""Isolate UUID admission from session binding and structural receipt generation."""

import pytest

from controlstudio_host import execution_broker as broker

CANONICAL_ID = "d89aa547-963a-4a25-b01c-52789c7e0fbc"
MISSING = object()
INVALID_IDS = [
    pytest.param(MISSING, id="missing"),
    pytest.param(None, id="null"),
    pytest.param(CANONICAL_ID.upper(), id="uppercase"),
    pytest.param(CANONICAL_ID.replace("-", ""), id="unhyphenated"),
    pytest.param("{" + CANONICAL_ID + "}", id="braced"),
    pytest.param("invalid", id="malformed"),
    pytest.param(False, id="boolean"),
    pytest.param(3, id="integer"),
]


class SessionBoundBroker(broker.MCPExecutionBroker):
    @classmethod
    def _validate_session_result_binding(cls, result, **kwargs):
        # Session binding is independently tested; exercise the actual projection.
        return result


def snapshot_result(identifier):
    result = {
        "operation": "project.snapshot",
        "ok": True,
        "verified": True,
        "project_unchanged": True,
        "operation_id": CANONICAL_ID,
        "programs": [],
        "program_count": 0,
        "total_visual_rows": 0,
    }
    if identifier is not MISSING:
        result["snapshot_id"] = identifier
    return result


def project_snapshot(identifier):
    return SessionBoundBroker._snapshot_result_projection(
        snapshot_result(identifier),
        expected_session_identity=None,
        request_arguments={"schema_version": "1.0"},
    )


def test_snapshot_accepts_canonical_uuid():
    assert project_snapshot(CANONICAL_ID)["snapshot_id"] == CANONICAL_ID


@pytest.mark.parametrize("identifier", INVALID_IDS)
def test_snapshot_rejects_noncanonical_uuid(identifier):
    with pytest.raises(broker.MCPToolError, match="snapshot result is invalid"):
        project_snapshot(identifier)


@pytest.fixture
def structure_projection(monkeypatch):
    identity = dict.fromkeys(broker.SESSION_IDENTITY_FIELDS, "synthetic")
    identity["project_hash"] = "a" * 64
    calls = []
    receipt = {"synthetic_receipt": True}

    def build_contract(*args, **kwargs):
        calls.append((args, kwargs))
        return receipt

    monkeypatch.setattr(broker, "build_structural_evidence_contract", build_contract)

    def project(identifier):
        result = {
            "session_id": identity["session_id"],
            "operation": "project.structure_index",
            "ok": True,
            "verified": True,
            "structure_index": {"index_digest": "b" * 64},
            "index_digest": "b" * 64,
            "receipt_path": "synthetic-receipt.json",
            "project_hash_before": identity["project_hash"],
            "project_hash_after": identity["project_hash"],
            "project_unchanged": True,
        }
        if identifier is not MISSING:
            result["operation_id"] = identifier
        return broker.MCPExecutionBroker._project_structure_index_evidence_contract(
            result, expected_session_identity=identity, request_arguments={},
        )

    return project, calls, receipt


def test_structure_index_accepts_canonical_uuid(structure_projection):
    project, calls, receipt = structure_projection
    assert project(CANONICAL_ID) is receipt
    assert len(calls) == 1
    assert calls[0][0][0] == "project.structure_index"


@pytest.mark.parametrize("identifier", INVALID_IDS)
def test_structure_index_rejects_noncanonical_uuid_before_receipt(
    structure_projection, identifier,
):
    project, calls, _ = structure_projection
    with pytest.raises(broker.MCPToolError, match="project structure index envelope"):
        project(identifier)
    assert calls == []
