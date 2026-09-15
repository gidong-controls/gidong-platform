from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from .api_models import LocalWorkspaceV1, NewBuildWorkspaceProvenanceV1

_ROT_PROOF_FIELDS = {
    "method",
    "same_active_object",
    "stable_repeat",
    "pid",
    "main_hwnd",
    "project_tree_hwnd",
    "project_path",
    "project_hash",
    "process_create_time",
    "version",
}
_NATIVE_FILE_PROOF_FIELDS = _ROT_PROOF_FIELDS - {"project_tree_hwnd"}
_WORKSPACE_FIELDS = {
    "workspace_id",
    "project_path",
    "project_hash",
    "source_sha256",
    "project_identity_digest",
}
_RESUME_FIELDS = {
    "purpose",
    "native_project_id",
    "trial_id",
    "receipt_digest",
    "source_kind",
    "provenance_receipt_digest",
    "workspace_id",
    "revision_id",
    "pid",
    "main_hwnd",
    "project_path",
    "project_hash",
    "process_create_time",
    "authorization_id",
}


def _path(value: Any) -> str:
    return os.path.normcase(str(Path(str(value)).expanduser().resolve()))


def _workspace_digest(workspace: LocalWorkspaceV1) -> str:
    payload = {
        "schema": "controlstudio-workspace-project-identity/v1",
        "workspace_id": workspace.workspace_id,
        "working_copy_path": _path(workspace.working_copy.path),
        "source_sha256": workspace.source.sha256.casefold(),
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _selected_pid_identity_proof_is_strong(proof: dict[str, Any]) -> bool:
    """Recognize both exact identity proofs emitted by the MCP adapter."""

    method = proof.get("method")
    if method == "same_rot_object_project_tree_and_path_v1":
        return bool(
            set(proof) == _ROT_PROOF_FIELDS
            and proof.get("same_active_object") is True
            and isinstance(proof.get("project_tree_hwnd"), int)
            and proof["project_tree_hwnd"] > 0
        )
    if method == "selected_pid_exact_window_title_native_file_use_v1":
        return bool(
            set(proof) == _NATIVE_FILE_PROOF_FIELDS
            and proof.get("same_active_object") is False
        )
    return False


def native_trial_resume_matches_expected(
    observed: Any,
    expected: dict[str, Any],
) -> bool:
    """Compare a resumed identity while tolerating OS timestamp precision.

    MCP independently reads the process creation timestamp and already rejects
    a difference of one millisecond or more from the Host-signed value.  Do not
    subsequently require bit-identical floating-point serialization.
    """

    if not isinstance(observed, dict) or set(observed) != set(expected):
        return False
    try:
        observed_time = float(observed["process_create_time"])
        expected_time = float(expected["process_create_time"])
    except (KeyError, TypeError, ValueError):
        return False
    if abs(observed_time - expected_time) >= 0.001:
        return False
    return all(
        observed.get(field) == value
        for field, value in expected.items()
        if field != "process_create_time"
    )


def native_trial_session_identity(
    session: Any,
    workspace: LocalWorkspaceV1,
) -> dict[str, Any] | None:
    """Return the exact MCP-observed/signed identity, or fail closed."""

    if not isinstance(session, dict):
        return None
    metadata = session.get("metadata")
    if not isinstance(metadata, dict):
        return None
    proof = metadata.get("project_identity_proof")
    workspace_identity = metadata.get("workspace_identity")
    resume = metadata.get("native_trial_resume")
    if not all(isinstance(item, dict) for item in (proof, workspace_identity, resume)):
        return None
    assert isinstance(proof, dict)
    assert isinstance(workspace_identity, dict)
    assert isinstance(resume, dict)
    provenance = workspace.provenance
    try:
        pid, hwnd = int(session["pid"]), int(session["hwnd"])
        project_path = _path(workspace.working_copy.path)
        project_hash = workspace.working_copy.current_sha256.casefold()
        valid = (
            workspace.source_kind == "new_build"
            and isinstance(provenance, NewBuildWorkspaceProvenanceV1)
            and _selected_pid_identity_proof_is_strong(proof)
            and proof["stable_repeat"] is True
            and proof["pid"] == pid
            and proof["main_hwnd"] == hwnd
            and _path(proof["project_path"]) == project_path
            and str(proof["project_hash"]).casefold() == project_hash
            and float(proof["process_create_time"]) > 0
            and (proof["version"] is None or isinstance(proof["version"], str))
            and set(workspace_identity) == _WORKSPACE_FIELDS
            and workspace_identity
            == {
                "workspace_id": workspace.workspace_id,
                "project_path": project_path,
                "project_hash": project_hash,
                "source_sha256": workspace.source.sha256.casefold(),
                "project_identity_digest": _workspace_digest(workspace),
            }
            and set(resume) == _RESUME_FIELDS
            and resume["native_project_id"] == provenance.native_project_id
            and str(uuid.UUID(resume["native_project_id"])) == resume["native_project_id"]
            and str(uuid.UUID(resume["trial_id"])) == resume["trial_id"]
            and str(uuid.UUID(resume["authorization_id"])) == resume["authorization_id"]
            and re.fullmatch(r"[0-9a-f]{64}", str(resume["receipt_digest"])) is not None
            and resume["purpose"] == "native_trial"
            and resume["source_kind"] == "new_build"
            and resume["provenance_receipt_digest"] == provenance.receipt_digest
            and resume["workspace_id"] == workspace.workspace_id
            and resume["revision_id"] == workspace.revision_id
            and resume["pid"] == pid
            and resume["main_hwnd"] == hwnd
            and _path(resume["project_path"]) == project_path
            and str(resume["project_hash"]).casefold() == project_hash
            and float(resume["process_create_time"]) == float(proof["process_create_time"])
            and _path(session["project_path"]) == project_path
            and str(session["project_hash"]).casefold() == project_hash
            and isinstance(metadata.get("title"), str)
            and bool(metadata["title"])
            and metadata.get("project_identity_source") == "host_authorized_native_trial_resume"
            and metadata.get("project_identity_verified_against_selected_pid") is True
            and metadata.get("project_identity_read_only_bound_to_selected_pid") is True
            and metadata.get("project_identity_assurance") == "controlled_launch_read_only"
        )
    except (AttributeError, KeyError, TypeError, ValueError, OSError):
        return None
    if not valid:
        return None
    return {
        "session_id": session.get("session_id"),
        "pid": pid,
        "hwnd": hwnd,
        "project_path": project_path,
        "project_hash": project_hash,
        "title": metadata["title"],
        "project_identity_proof": deepcopy(proof),
        "workspace_identity": deepcopy(workspace_identity),
        "native_trial_resume": deepcopy(resume),
    }


async def fresh_native_trial_identity(
    mcp: Any,
    cached_workspace: LocalWorkspaceV1,
    cached_session: dict[str, Any],
) -> tuple[LocalWorkspaceV1, dict[str, Any]]:
    """Read both MCP authorities and reject any cached-to-live identity drift."""

    cached_identity = native_trial_session_identity(cached_session, cached_workspace)
    if cached_identity is None:
        raise ValueError("cached native trial identity is invalid")
    raw_workspace = await mcp.call_workspace_tool(
        "workspace.status", {"workspace_id": cached_workspace.workspace_id}
    )
    raw_session = await mcp.call_read_tool(
        "xg5000.session_status", {"session_id": cached_session.get("session_id")}
    )
    if not isinstance(raw_workspace, dict):
        raise TypeError("fresh workspace identity is invalid")
    workspace_fields = {
        name: raw_workspace[name] for name in LocalWorkspaceV1.model_fields if name in raw_workspace
    }
    fresh_workspace = LocalWorkspaceV1.model_validate(workspace_fields)
    fresh_session = (
        raw_session["session"]
        if isinstance(raw_session, dict) and isinstance(raw_session.get("session"), dict)
        else raw_session
    )
    fresh_identity = native_trial_session_identity(fresh_session, fresh_workspace)
    if (
        not isinstance(fresh_session, dict)
        or fresh_workspace.model_dump(mode="json") != cached_workspace.model_dump(mode="json")
        or fresh_identity != cached_identity
    ):
        raise ValueError("native trial workspace or session identity drifted")
    return fresh_workspace, fresh_session
