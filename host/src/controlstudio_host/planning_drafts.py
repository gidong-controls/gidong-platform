"""Durable, Host-owned work surface for incremental new-project design.

The draft is deliberately not an execution authority.  It may be incomplete and
invalid while the Agent works on it.  Only ``publish_review`` performs the full
native compiler check and returns a revision that can be converted into the
existing immutable project-plan artifact.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from controlstudio_contracts import AgentToolDefinitionV1
from pydantic import ValidationError

from .api_models import (
    ProjectBriefV1,
    ProjectPlanIndexAcceptanceBindingsV1,
    ProjectPlanNativeDesignV1,
    ProjectPlanTechnicalConfigurationV1,
    ProjectRequirementSourceV1,
)
from .native_projects import NativeProjectPreconditionError, _compile_native_program
from .project_planning import validate_requirement_sources
from .recovery_conditions import blocked_recovery_conditions

# One draft or selected-section response must fit inside the existing 2 MB
# Host/sidecar NDJSON frame, including its protocol envelope. This still leaves
# roughly a 200k-token ASCII work product while retaining a safe framing margin.
_MAX_DRAFT_BYTES = 1_400_000
_MAX_DIAGNOSTICS = 100
# Keep each model-authored mutation small enough to finish as one valid tool
# call on local models.  This is an atomic batch limit, not a project-size
# limit: the retained draft can accumulate any number of batches up to the
# 1.4 MB durable-document boundary above.
_MAX_SECTION_CHANGES = 4

# A diagnostic may be reported at its owning contract while the repair belongs
# in executable ladder code. Use the same ownership for model context and edits.
_LADDER_REPAIR_CODES = frozenset({
    "alarm_reset_missing_recovery_conditions",
    "unreviewed_signal_set", "unreviewed_signal_reset",
    "alarm_set_missing_trigger_conditions", "alarm_missing_latch_set_rung",
    "sequence_start_rung_missing_or_incomplete",
    "sequence_transition_rung_missing_or_incomplete",
    "sequence_reset_rung_missing_or_incomplete",
    "recovery_sequence_reset_rung_missing_or_incomplete",
    "increment_reset_writer_mismatch", "increment_reset_not_mutually_exclusive",
    "control_network_out_missing_effective_permits",
    "index_acceptance_mode_selection_not_retained_until_stopped",
    "index_acceptance_automatic_start_rearm_missing",
    "index_acceptance_active_sequence_blocked_by_stopped_signal",
    "index_acceptance_motion_not_gated_by_running",
    "index_acceptance_timeout_reset_blocked_by_done_mirror",
    "index_acceptance_manual_stop_reset_can_resume_motion",
})

_COLLECTIONS = (
    "io_points",
    "operating_modes",
    "control_networks",
    "ladder_rungs",
    "timers",
    "sequences",
    "alarms",
    "recovery_rules",
    "safety_rules",
)
_EDITABLE_SECTIONS = ("narrative", "technical_configuration", "index_acceptance_bindings", *_COLLECTIONS)
_IDENTITY_FIELDS = {
    "io_points": "signal_id",
    "operating_modes": "mode_id",
    "control_networks": "network_id",
    "ladder_rungs": "rung_id",
    "timers": "timer_id",
    "sequences": "sequence_id",
    "alarms": "alarm_id",
    "recovery_rules": "recovery_id",
    "safety_rules": "rule_id",
}
_SCOPE_AUTHORITY_FIELDS = {
    "io_points": ("hardware_and_io",),
    "operating_modes": ("operating_modes",),
    "control_networks": (
        "hardware_and_io",
        "sequence",
        "safety_and_interlocks",
    ),
    "timers": ("sequence", "performance_and_acceptance"),
    "sequences": ("sequence",),
    "alarms": ("sequence", "safety_and_interlocks"),
    "recovery_rules": ("sequence", "safety_and_interlocks"),
    "safety_rules": ("safety_and_interlocks",),
}
_NA_BASES = {
    "operating_modes": "single_mode_operation",
    "timers": "no_timing_behavior",
    "sequences": "no_sequence_behavior",
    "alarms": "no_alarm_behavior",
    "recovery_rules": "no_recovery_behavior",
    "safety_rules": "no_physical_or_hazard_outputs",
}


def _json_dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def _json_clone(value: Any) -> Any:
    return json.loads(_json_dump(value))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _brief_is_internal_simulation(brief: ProjectBriefV1) -> bool:
    brief_text = "\n".join(
        str(value) for value in brief.model_dump(mode="python").values() if isinstance(value, str)
    ).casefold()
    internal_memory = "내부" in brief_text and any(
        marker in brief_text for marker in (" m ", "m 신호", "m 주소", "internal")
    )
    simulator = any(
        marker in brief_text
        for marker in ("모의", "시뮬레이션", "xg-sim", "xgsim", "simulation")
    )
    return (internal_memory and simulator) or any(
        marker in brief_text
        for marker in (
            "내부 모의",
            "실제 배선 없이",
            "internal simulation",
            "internal-only",
            "without physical wiring",
        )
    )


def _initial_document(project_name: str) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "project_name": project_name,
        "narrative": {},
        "technical_configuration": None,
        "native_design": {
            "schema_version": "2",
            "collection_scope": None,
            **{name: [] for name in _COLLECTIONS},
        },
        "remaining_work": [
            "narrative",
            "technical_configuration",
            *_COLLECTIONS,
            "full_check",
        ],
    }


@dataclass(frozen=True)
class ProjectDesignDraft:
    conversation_id: str
    revision: int
    brief_digest: str
    document: dict[str, Any]
    diagnostics: list[dict[str, Any]]
    status: str
    published_digest: str | None
    updated_by_run_id: str
    updated_at: str


class ProjectDesignDraftConflict(RuntimeError):
    pass


class ProjectDesignDraftRepository:
    """Append-only revisions plus one current-row projection in the Host DB."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS project_design_drafts (
                    conversation_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    brief_digest TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    diagnostics_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    published_digest TEXT,
                    updated_by_run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_design_draft_revisions (
                    conversation_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    brief_digest TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    diagnostics_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    published_digest TEXT,
                    updated_by_run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (conversation_id, revision)
                );
                """
            )
            connection.commit()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ProjectDesignDraft:
        return ProjectDesignDraft(
            conversation_id=str(row["conversation_id"]),
            revision=int(row["revision"]),
            brief_digest=str(row["brief_digest"]),
            document=json.loads(str(row["document_json"])),
            diagnostics=json.loads(str(row["diagnostics_json"])),
            status=str(row["status"]),
            published_digest=(
                str(row["published_digest"]) if row["published_digest"] is not None else None
            ),
            updated_by_run_id=str(row["updated_by_run_id"]),
            updated_at=str(row["updated_at"]),
        )

    def get(self, conversation_id: str) -> ProjectDesignDraft | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM project_design_drafts WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def get_revision(
        self, conversation_id: str, revision: int
    ) -> ProjectDesignDraft | None:
        """Read one immutable revision owned by the same planning conversation."""

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT *, created_at AS updated_at
                FROM project_design_draft_revisions
                WHERE conversation_id = ? AND revision = ?
                """,
                (conversation_id, revision),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def narrative_adoption_boundary_message_id(
        self,
        conversation_id: str,
        *,
        brief_digest: str,
        retained_narrative: Mapping[str, Any],
    ) -> str | None:
        """Resolve the request that last changed the current same-brief narrative."""

        target = _json_dump(dict(retained_narrative))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT revision, brief_digest, document_json, updated_by_run_id
                FROM project_design_draft_revisions
                WHERE conversation_id = ?
                ORDER BY revision ASC
                """,
                (conversation_id,),
            ).fetchall()
            previous: str | None = None
            adoption_run_id: str | None = None
            for row in rows:
                document = json.loads(str(row["document_json"]))
                narrative = document.get("narrative")
                encoded = _json_dump(narrative) if isinstance(narrative, dict) else None
                if (
                    encoded == target
                    and encoded != previous
                    and str(row["brief_digest"]) == brief_digest
                ):
                    adoption_run_id = str(row["updated_by_run_id"])
                previous = encoded
            if adoption_run_id is None:
                return None
            try:
                boundary = connection.execute(
                    """
                    SELECT runs.request_message_id
                    FROM runs
                    JOIN messages ON messages.id = runs.request_message_id
                    WHERE runs.id = ?
                      AND runs.conversation_id = ?
                      AND messages.conversation_id = ?
                      AND messages.role = 'user'
                    """,
                    (adoption_run_id, conversation_id, conversation_id),
                ).fetchone()
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc):
                    raise
                return None
        return (
            str(boundary["request_message_id"])
            if boundary is not None and boundary["request_message_id"] is not None
            else None
        )

    def has_structured_brief_after_message(
        self, conversation_id: str, message_id: str
    ) -> bool | None:
        """Return whether typed brief authority changed after an adoption boundary."""

        with self._connection() as connection:
            try:
                boundary = connection.execute(
                    """SELECT sequence FROM messages
                       WHERE id = ? AND conversation_id = ? AND role = 'user'""",
                    (message_id, conversation_id),
                ).fetchone()
                if boundary is None:
                    return None
                changed = connection.execute(
                    """SELECT 1 FROM messages
                       WHERE conversation_id = ? AND sequence > ?
                         AND json_extract(metadata_json, '$.structured_project_brief') = 1
                       LIMIT 1""",
                    (conversation_id, int(boundary["sequence"])),
                ).fetchone()
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc) and "no such column" not in str(exc):
                    raise
                return None
        return changed is not None

    def latest_published_revision(
        self, conversation_id: str
    ) -> ProjectDesignDraft | None:
        """Read the newest immutable published base for one conversation."""

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT *, created_at AS updated_at
                FROM project_design_draft_revisions
                WHERE conversation_id = ?
                  AND status = 'published'
                  AND published_digest IS NOT NULL
                ORDER BY revision DESC
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def published_revision_for_digest(
        self, conversation_id: str, published_digest: str
    ) -> ProjectDesignDraft | None:
        """Read the exact same-conversation publication named by its plan digest."""

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT *, created_at AS updated_at
                FROM project_design_draft_revisions
                WHERE conversation_id = ?
                  AND status = 'published'
                  AND published_digest = ?
                ORDER BY revision DESC
                LIMIT 1
                """,
                (conversation_id, published_digest),
            ).fetchone()
            if row is None:
                try:
                    row = connection.execute(
                        """
                        SELECT revisions.*, revisions.created_at AS updated_at
                        FROM project_design_draft_revisions AS revisions
                        JOIN messages
                          ON messages.conversation_id = revisions.conversation_id
                         AND json_extract(messages.metadata_json, '$.run_id') = revisions.updated_by_run_id
                        WHERE revisions.conversation_id = ?
                          AND revisions.status = 'published'
                          AND messages.role = 'assistant'
                          AND json_extract(messages.metadata_json, '$.plan_digest') = ?
                          AND json_type(messages.metadata_json, '$.plan_artifact') = 'object'
                        ORDER BY revisions.revision DESC
                        LIMIT 1
                        """,
                        (conversation_id, published_digest),
                    ).fetchone()
                except sqlite3.OperationalError as exc:
                    if "no such table: messages" not in str(exc):
                        raise
        return self._from_row(row) if row is not None else None

    def revision_summary(self, conversation_id: str) -> dict[str, Any]:
        """Return bounded revision availability without exposing other conversations."""

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT MIN(revision) AS minimum_revision,
                       MAX(revision) AS maximum_revision,
                       COUNT(*) AS revision_count,
                       MAX(CASE WHEN status = 'published' AND published_digest IS NOT NULL
                                THEN revision END) AS latest_published_revision
                FROM project_design_draft_revisions
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
        assert row is not None
        return {
            "minimumRevision": row["minimum_revision"],
            "maximumRevision": row["maximum_revision"],
            "revisionCount": int(row["revision_count"]),
            "latestPublishedRevision": row["latest_published_revision"],
        }

    def prior_run_revision_with_ladder(
        self,
        conversation_id: str,
        run_id: str,
        before_revision: int,
        ladder_rungs: Sequence[Mapping[str, Any]],
    ) -> int | None:
        """Find an exact ladder state already stored by this run."""

        expected = _json_dump(list(ladder_rungs))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT revision, document_json
                FROM project_design_draft_revisions
                WHERE conversation_id = ?
                  AND revision < ?
                  AND (
                    updated_by_run_id = ?
                    OR revision = (
                      SELECT MIN(current_run.revision) - 1
                      FROM project_design_draft_revisions AS current_run
                      WHERE current_run.conversation_id = ?
                        AND current_run.updated_by_run_id = ?
                    )
                  )
                ORDER BY revision DESC
                """,
                (
                    conversation_id,
                    before_revision,
                    run_id,
                    conversation_id,
                    run_id,
                ),
            ).fetchall()
        for row in rows:
            try:
                document = json.loads(str(row["document_json"]))
                prior = document["native_design"]["ladder_rungs"]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if _json_dump(prior) == expected:
                return int(row["revision"])
        return None

    def ensure(
        self,
        *,
        conversation_id: str,
        brief_digest: str,
        project_name: str,
        run_id: str,
    ) -> ProjectDesignDraft:
        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM project_design_drafts WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                document = _initial_document(project_name)
                encoded = _json_dump(document)
                diagnostics = _json_dump([])
                connection.execute(
                    """
                    INSERT INTO project_design_drafts(
                        conversation_id, revision, brief_digest, document_json,
                        diagnostics_json, status, published_digest,
                        updated_by_run_id, created_at, updated_at
                    ) VALUES (?, 0, ?, ?, ?, 'working', NULL, ?, ?, ?)
                    """,
                    (conversation_id, brief_digest, encoded, diagnostics, run_id, now, now),
                )
                connection.execute(
                    """
                    INSERT INTO project_design_draft_revisions VALUES
                    (?, 0, ?, ?, ?, 'working', NULL, ?, ?)
                    """,
                    (conversation_id, brief_digest, encoded, diagnostics, run_id, now),
                )
            elif str(row["brief_digest"]) != brief_digest:
                document = json.loads(str(row["document_json"]))
                revision = int(row["revision"]) + 1
                diagnostic_values = [
                    {
                        "code": "brief_changed",
                        "path": "project_brief",
                        "message": "The stored brief changed; inspect affected sections and run design.check.",
                    }
                ]
                encoded = _json_dump(document)
                diagnostics = _json_dump(diagnostic_values)
                connection.execute(
                    """
                    UPDATE project_design_drafts SET revision = ?, brief_digest = ?,
                        diagnostics_json = ?, status = 'working', published_digest = NULL,
                        updated_by_run_id = ?, updated_at = ?
                    WHERE conversation_id = ?
                    """,
                    (revision, brief_digest, diagnostics, run_id, now, conversation_id),
                )
                connection.execute(
                    """
                    INSERT INTO project_design_draft_revisions VALUES
                    (?, ?, ?, ?, ?, 'working', NULL, ?, ?)
                    """,
                    (conversation_id, revision, brief_digest, encoded, diagnostics, run_id, now),
                )
            connection.commit()
        result = self.get(conversation_id)
        assert result is not None
        return result

    def update(
        self,
        *,
        conversation_id: str,
        expected_revision: int,
        brief_digest: str,
        document: dict[str, Any],
        diagnostics: Sequence[Mapping[str, Any]],
        status: str,
        published_digest: str | None,
        run_id: str,
    ) -> ProjectDesignDraft:
        encoded = _json_dump(document)
        if len(encoded.encode("utf-8")) > _MAX_DRAFT_BYTES:
            raise ProjectDesignDraftConflict("design draft exceeds its durable size bound")
        diagnostic_json = _json_dump(list(diagnostics)[:_MAX_DIAGNOSTICS])
        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision, brief_digest FROM project_design_drafts WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                raise ProjectDesignDraftConflict("design draft does not exist")
            if int(row["revision"]) != expected_revision:
                raise ProjectDesignDraftConflict(
                    f"draft revision changed; expected {expected_revision}, current {row['revision']}"
                )
            if str(row["brief_digest"]) != brief_digest:
                raise ProjectDesignDraftConflict("design draft brief binding changed")
            revision = expected_revision + 1
            connection.execute(
                """
                UPDATE project_design_drafts SET revision = ?, document_json = ?,
                    diagnostics_json = ?, status = ?, published_digest = ?,
                    updated_by_run_id = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (
                    revision,
                    encoded,
                    diagnostic_json,
                    status,
                    published_digest,
                    run_id,
                    now,
                    conversation_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO project_design_draft_revisions VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    revision,
                    brief_digest,
                    encoded,
                    diagnostic_json,
                    status,
                    published_digest,
                    run_id,
                    now,
                ),
            )
            connection.commit()
        result = self.get(conversation_id)
        assert result is not None
        return result


def _diagnostic(
    code: str,
    path: str,
    message: str,
    *,
    received: Any = None,
    expected_type: str | None = None,
    related_ids: Sequence[str] = (),
) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "path": path, "message": message}
    if received is not None:
        result["received"] = received
    if expected_type is not None:
        result["expectedType"] = expected_type
    if related_ids:
        result["relatedIds"] = list(related_ids)[:30]
    return result


def _reference_repair_section(diagnostic: Mapping[str, Any]) -> str | None:
    """Return the declaration collection that owns a missing typed reference."""
    code = str(diagnostic.get("code") or "")
    expected = str(diagnostic.get("expectedType") or "").casefold()
    path = str(diagnostic.get("path") or "")
    if code == "unknown_alarm_latch_reference":
        return "io_points"
    if code != "unknown_reference":
        return None
    # A bad identifier inside an existing rung is normally repaired in place;
    # treating every ladder typo as a missing I/O declaration would preserve the
    # typo and manufacture a new signal. Declarative contracts, by contrast,
    # establish the signals/timers/sequences the ladder must implement.
    if path.startswith("/ladderRungs") or path.startswith("/native_design/ladder_rungs"):
        return None
    if "signal" in expected:
        return "io_points"
    if "timer" in expected:
        return "timers"
    if "step" in expected or "sequence" in expected:
        return "sequences"
    return None


def _validation_diagnostics(exc: ValidationError) -> list[dict[str, Any]]:
    return [
        _diagnostic(
            "schema_invalid",
            "/" + "/".join(str(part) for part in item["loc"]),
            str(item.get("msg") or "schema validation failed"),
            received=item.get("input"),
        )
        for item in exc.errors(include_url=False, include_input=True)[:_MAX_DIAGNOSTICS]
    ]


def _reference_diagnostics(design: dict[str, Any]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    io_ids = {str(item["signalId"]) for item in design["ioPoints"]}
    io_kind_by_id = {str(item["signalId"]): str(item["kind"]) for item in design["ioPoints"]}
    writable_ids = {
        str(item["signalId"])
        for item in design["ioPoints"]
        if item["kind"] in {"digital_output", "internal_bit"}
    }
    actuator_output_ids = {
        str(item["signalId"])
        for item in design["ioPoints"]
        if item["kind"] == "digital_output"
        and not str(item["signalId"]).casefold().startswith("alarm_")
    }
    operator_reset_ids = {
        str(item["signalId"])
        for item in design["ioPoints"]
        if item["kind"] == "digital_input" and "reset" in str(item["signalId"]).casefold()
    }
    timer_ids = {str(item["timerId"]) for item in design["timers"]}
    mode_ids = {str(item["modeId"]) for item in design["operatingModes"]}
    sequence_ids = {str(item["sequenceId"]) for item in design["sequences"]}
    step_ids = {
        str(step["stepId"]) for sequence in design["sequences"] for step in sequence["steps"]
    }
    alarm_ids = {str(item["alarmId"]) for item in design["alarms"]}
    sources = {
        "signal": io_ids,
        "timer": timer_ids,
        "step": step_ids,
        "sequence_reset": sequence_ids,
    }

    def guarantees(value: Mapping[str, Any], required: tuple[str, str, str]) -> bool:
        if value.get("kind") == "contact":
            return (
                value.get("sourceType"),
                value.get("sourceId"),
                value.get("state"),
            ) == required
        terms = value.get("terms")
        if not isinstance(terms, list) or any(not isinstance(term, dict) for term in terms):
            return False
        if value.get("kind") == "all_of":
            return any(guarantees(term, required) for term in terms)
        if value.get("kind") == "any_of":
            return all(guarantees(term, required) for term in terms)
        return False

    def semantic_requirement(value: Mapping[str, Any]) -> tuple[str, str, str]:
        source_id = str(value["signalId"])
        return (
            "timer" if source_id in timer_ids else "signal",
            source_id,
            str(value["state"]),
        )

    def guaranteed_contacts(value: Mapping[str, Any]) -> set[tuple[str, str, str]]:
        if value.get("kind") == "contact":
            return {
                (
                    str(value.get("sourceType")),
                    str(value.get("sourceId")),
                    str(value.get("state")),
                )
            }
        children = [
            guaranteed_contacts(term) for term in value.get("terms") or [] if isinstance(term, dict)
        ]
        if not children:
            return set()
        if value.get("kind") == "all_of":
            return set().union(*children)
        return set.intersection(*children)

    def positive_step_contacts(value: Mapping[str, Any]) -> set[str]:
        if value.get("kind") == "contact":
            return (
                {str(value.get("sourceId"))}
                if value.get("sourceType") == "step" and value.get("state") == "on"
                else set()
            )
        return set().union(
            *(
                positive_step_contacts(term)
                for term in value.get("terms") or []
                if isinstance(term, dict)
            )
        )

    def require(value: Any, allowed: set[str], path: str, expected: str) -> None:
        if not isinstance(value, str) or value not in allowed:
            diagnostics.append(
                _diagnostic(
                    "unknown_reference",
                    path,
                    f"Reference is not declared as {expected}.",
                    received=value,
                    expected_type=expected,
                    related_ids=sorted(allowed),
                )
            )

    def condition(value: dict[str, Any], path: str) -> None:
        if value.get("kind") == "contact":
            source_type = value.get("sourceType")
            allowed = sources.get(str(source_type), set())
            require(value.get("sourceId"), allowed, f"{path}/sourceId", str(source_type))
            return
        for index, term in enumerate(value.get("terms") or []):
            condition(term, f"{path}/terms/{index}")

    for index, mode in enumerate(design["operatingModes"]):
        require(
            mode["activation"]["signalId"],
            io_ids,
            f"/operatingModes/{index}/activation/signalId",
            "signal",
        )
        for position, target in enumerate(mode["allowedTransitions"]):
            require(
                target, mode_ids, f"/operatingModes/{index}/allowedTransitions/{position}", "mode"
            )
    for index, network in enumerate(design["controlNetworks"]):
        require(
            network["outputSignalId"],
            writable_ids,
            f"/controlNetworks/{index}/outputSignalId",
            "writable signal",
        )
        if network.get("modeId") is not None:
            require(network["modeId"], mode_ids, f"/controlNetworks/{index}/modeId", "mode")
        if network.get("stepId") is not None:
            require(network["stepId"], step_ids, f"/controlNetworks/{index}/stepId", "step")
        for position, item in enumerate(network["permitAll"]):
            require(
                item["signalId"],
                io_ids | timer_ids,
                f"/controlNetworks/{index}/permitAll/{position}/signalId",
                "signal or timer",
            )
    for index, timer in enumerate(design["timers"]):
        if timer.get("modeId") is not None:
            require(timer["modeId"], mode_ids, f"/timers/{index}/modeId", "mode")
        if timer.get("stepId") is not None:
            require(timer["stepId"], step_ids, f"/timers/{index}/stepId", "step")
        for position, item in enumerate(timer["enableAll"]):
            require(
                item["signalId"],
                io_ids | timer_ids,
                f"/timers/{index}/enableAll/{position}/signalId",
                "signal or timer",
            )
    seen_step_ids: dict[str, tuple[int, int]] = {}
    seen_step_addresses: dict[str, tuple[int, int]] = {}
    for index, sequence in enumerate(design["sequences"]):
        if sequence.get("modeId") is not None:
            require(sequence["modeId"], mode_ids, f"/sequences/{index}/modeId", "mode")
        for field in ("startAll", "resetAll"):
            seen_states: dict[str, str] = {}
            for position, item in enumerate(sequence[field]):
                require(
                    item["signalId"],
                    io_ids | timer_ids,
                    f"/sequences/{index}/{field}/{position}/signalId",
                    "signal or timer",
                )
                signal_id = str(item["signalId"])
                state = str(item["state"])
                if signal_id in seen_states and seen_states[signal_id] != state:
                    diagnostics.append(
                        _diagnostic(
                            "contradictory_condition",
                            f"/sequences/{index}/{field}/{position}",
                            f"{field} cannot require {signal_id} both on and off. Remove the incorrect condition.",
                            received={
                                "signalId": signal_id,
                                "states": [seen_states[signal_id], state],
                            },
                            related_ids=[str(sequence["sequenceId"])],
                        )
                    )
                seen_states.setdefault(signal_id, state)
        for step_index, step in enumerate(sequence["steps"]):
            step_id = str(step["stepId"])
            address = str(step.get("address") or "")
            if step_id in seen_step_ids:
                diagnostics.append(
                    _diagnostic(
                        "duplicate_sequence_step_id",
                        f"/sequences/{index}/steps/{step_index}/stepId",
                        f"Sequence step ID {step_id!r} is already used; every sequence step needs a globally unique ID.",
                        received=step_id,
                        related_ids=[str(sequence["sequenceId"])],
                    )
                )
            else:
                seen_step_ids[step_id] = (index, step_index)
            if address:
                if address in seen_step_addresses:
                    diagnostics.append(
                        _diagnostic(
                            "duplicate_sequence_step_address",
                            f"/sequences/{index}/steps/{step_index}/address",
                            f"Sequence step address {address!r} is already used; allocate a different S group or step number.",
                            received=address,
                            related_ids=[str(sequence["sequenceId"]), step_id],
                        )
                    )
                elif address:
                    seen_step_addresses[address] = (index, step_index)
            seen_states = {}
            for position, item in enumerate(step["completeAll"]):
                require(
                    item["signalId"],
                    io_ids | timer_ids,
                    f"/sequences/{index}/steps/{step_index}/completeAll/{position}/signalId",
                    "signal or timer",
                )
                signal_id = str(item["signalId"])
                state = str(item["state"])
                if signal_id in seen_states and seen_states[signal_id] != state:
                    diagnostics.append(
                        _diagnostic(
                            "contradictory_condition",
                            f"/sequences/{index}/steps/{step_index}/completeAll/{position}",
                            f"completeAll cannot require {signal_id} both on and off. Remove the incorrect condition.",
                            received={
                                "signalId": signal_id,
                                "states": [seen_states[signal_id], state],
                            },
                            related_ids=[str(sequence["sequenceId"]), str(step["stepId"])],
                        )
                    )
                seen_states.setdefault(signal_id, state)
        for position, item in enumerate(sequence["startAll"]):
            signal_id = str(item["signalId"])
            if io_kind_by_id.get(signal_id) == "digital_output":
                diagnostics.append(
                    _diagnostic(
                        "sequence_start_uses_driven_output",
                        f"/sequences/{index}/startAll/{position}/signalId",
                        "A sequence cannot start from a driven digital output because that creates a circular command dependency. Use operator input and readiness conditions.",
                        received=signal_id,
                        related_ids=[str(sequence["sequenceId"])],
                    )
                )
    for index, alarm in enumerate(design["alarms"]):
        latch_id = str(alarm["latchSignalId"])
        if latch_id not in writable_ids:
            alarm_id = str(alarm["alarmId"])
            dedicated_indicator_latch = (
                alarm_id if io_kind_by_id.get(alarm_id) == "internal_bit" else None
            )
            shared_latches = [
                str(item["latchSignalId"])
                for position, item in enumerate(design["alarms"])
                if position != index and str(item["latchSignalId"]) in writable_ids
            ]
            suggested_latch = dedicated_indicator_latch or (
                max(set(shared_latches), key=shared_latches.count) if shared_latches else None
            )
            diagnostics.append(
                _diagnostic(
                    "unknown_alarm_latch_reference",
                    f"/alarms/{index}/latchSignalId",
                    (
                        f"Alarm {alarm['alarmId']!r} references undeclared latch "
                        f"{latch_id!r}. Patch the existing alarm item "
                        f"{alarm['alarmId']!r}; do not create another alarm. "
                        + (
                            f"Use declared latchSignalId {suggested_latch!r}. "
                            "When the alarm indicator itself is an internal bit, it is the "
                            "preferred dedicated persistent latch; do not return to a latch "
                            "shared with another alarm."
                            if suggested_latch is not None
                            else "Use one declared writable latch signal."
                        )
                    ),
                    received={
                        "alarmId": alarm["alarmId"],
                        "invalidLatchSignalId": latch_id,
                        "suggestedLatchSignalId": suggested_latch,
                    },
                    expected_type="declared writable alarm latch",
                    related_ids=sorted(writable_ids),
                )
            )
        if alarm.get("modeId") is not None:
            require(alarm["modeId"], mode_ids, f"/alarms/{index}/modeId", "mode")
        if alarm.get("stepId") is not None:
            require(alarm["stepId"], step_ids, f"/alarms/{index}/stepId", "step")
        for position, output in enumerate(alarm["affectedOutputSignalIds"]):
            require(
                output,
                writable_ids,
                f"/alarms/{index}/affectedOutputSignalIds/{position}",
                "writable signal",
            )
        for position, item in enumerate(alarm["triggerAll"]):
            require(
                item["signalId"],
                io_ids | timer_ids,
                f"/alarms/{index}/triggerAll/{position}/signalId",
                "signal or timer",
            )
        latch_id = alarm["latchSignalId"]
        for position, output_id in enumerate(alarm["affectedOutputSignalIds"]):
            # `affectedOutputSignalIds` names process actuators that must become
            # safe.  Diagnose a self-referential alarm indicator here, before
            # suggesting an interlock for that indicator and trapping the
            # repair agent in a cross-section loop.
            if output_id == alarm["alarmId"]:
                diagnostics.append(
                    _diagnostic(
                        "alarm_affected_output_is_alarm_indicator",
                        f"/alarms/{index}/affectedOutputSignalIds/{position}",
                        (
                            f"Alarm {alarm['alarmId']!r} lists its own indicator signal "
                            "as an affected process output. Replace it with the actuator "
                            "or actuators that must deenergize while the alarm is latched."
                        ),
                        received={
                            "alarmId": alarm["alarmId"],
                            "outputSignalId": output_id,
                        },
                        expected_type="process actuator output",
                        related_ids=sorted(actuator_output_ids),
                    )
                )
                continue
            protected = any(
                rule["classification"] == "process_interlock"
                and isinstance(rule.get("permitWhen"), dict)
                and rule["permitWhen"].get("signalId") == latch_id
                and rule["permitWhen"].get("state") == "off"
                and output_id in rule["affectedOutputSignalIds"]
                for rule in design["safetyRules"]
            )
            if not protected:
                suggested_rule_id = f"safety_{alarm['alarmId']}_{output_id}"
                diagnostics.append(
                    _diagnostic(
                        "alarm_output_missing_latch_off_interlock",
                        f"/alarms/{index}/affectedOutputSignalIds/{position}",
                        (
                            f"Output {output_id!r} affected by alarm {alarm['alarmId']!r} "
                            f"requires a process_interlock safety rule whose permitWhen is "
                            f"signal {latch_id!r} in state 'off'. Add new rule "
                            f"{suggested_rule_id!r}; preserve every existing rule because "
                            "each independent alarm latch needs its own permit."
                        ),
                        received={
                            "alarmId": alarm["alarmId"],
                            "latchSignalId": latch_id,
                            "outputSignalId": output_id,
                            "suggestedRuleId": suggested_rule_id,
                        },
                        expected_type="alarm latch-off process interlock",
                        related_ids=[suggested_rule_id],
                    )
                )
    for index, recovery in enumerate(design["recoveryRules"]):
        for position, alarm_id in enumerate(recovery["resetAlarmIds"]):
            require(
                alarm_id, alarm_ids, f"/recoveryRules/{index}/resetAlarmIds/{position}", "alarm"
            )
        for position, sequence_id in enumerate(recovery["resetSequenceIds"]):
            require(
                sequence_id,
                sequence_ids,
                f"/recoveryRules/{index}/resetSequenceIds/{position}",
                "sequence",
            )
        for position, item in enumerate(recovery["whenAll"]):
            require(
                item["signalId"],
                io_ids | timer_ids,
                f"/recoveryRules/{index}/whenAll/{position}/signalId",
                "signal or timer",
            )
        recovery_pairs = {
            (str(item["signalId"]), str(item["state"])) for item in recovery["whenAll"]
        }
        for sequence_id in recovery["resetSequenceIds"]:
            sequence = next(
                (item for item in design["sequences"] if item["sequenceId"] == sequence_id),
                None,
            )
            if sequence is None or sequence["restartPolicy"] != "require_start_release":
                continue
            inverse_start_pairs = {
                (
                    str(item["signalId"]),
                    "off" if item["state"] == "on" else "on",
                )
                for item in sequence["startAll"]
            }
            if inverse_start_pairs.isdisjoint(recovery_pairs):
                diagnostics.append(
                    _diagnostic(
                        "recovery_missing_inverse_start_condition",
                        f"/recoveryRules/{index}/whenAll",
                        (
                            f"Recovery {recovery['recoveryId']!r} resets require_start_release "
                            f"sequence {sequence_id!r}; add at least one inverse start "
                            f"condition from {sorted(inverse_start_pairs)!r}."
                        ),
                        received=sorted(recovery_pairs),
                        expected_type="inverse sequence start condition",
                        related_ids=[sequence_id],
                    )
                )
    safety_effect_owners: dict[tuple[str, str, str, str, str], str] = {}
    safety_rules_by_output: dict[str, list[dict[str, Any]]] = {}
    for index, rule in enumerate(design["safetyRules"]):
        if rule.get("permitWhen") is not None:
            require(
                rule["permitWhen"]["signalId"],
                io_ids | timer_ids,
                f"/safetyRules/{index}/permitWhen/signalId",
                "signal or timer",
            )
            permit_signal_id = str(rule["permitWhen"]["signalId"])
            if (
                rule["classification"] == "process_interlock"
                and permit_signal_id in operator_reset_ids
            ):
                diagnostics.append(
                    _diagnostic(
                        "operator_reset_used_as_process_permit",
                        f"/safetyRules/{index}/permitWhen",
                        (
                            f"Safety rule {rule['ruleId']!r} uses momentary reset command "
                            f"{permit_signal_id!r} as its process permit. A reset command "
                            "acknowledges recovery; it is not a continuously valid safe "
                            "condition. Use the inverse unsafe-state signal, an alarm-latch "
                            "off condition, or remove the redundant rule."
                        ),
                        received={
                            "ruleId": rule["ruleId"],
                            "permitSignalId": permit_signal_id,
                        },
                        expected_type="persistent safe-state permit",
                        related_ids=sorted((io_ids | timer_ids) - operator_reset_ids),
                    )
                )
        for position, output in enumerate(rule["affectedOutputSignalIds"]):
            require(
                output,
                writable_ids,
                f"/safetyRules/{index}/affectedOutputSignalIds/{position}",
                "writable signal",
            )
            safety_rules_by_output.setdefault(str(output), []).append(rule)
            if output in alarm_ids:
                diagnostics.append(
                    _diagnostic(
                        "safety_rule_targets_alarm_indicator",
                        f"/safetyRules/{index}/affectedOutputSignalIds/{position}",
                        (
                            f"Safety rule {rule['ruleId']!r} targets alarm indicator "
                            f"{output!r}. Process interlocks must deenergize actuators, "
                            "not suppress the indicator that reports the alarm."
                        ),
                        received={
                            "ruleId": rule["ruleId"],
                            "outputSignalId": output,
                        },
                        expected_type="process actuator output",
                        related_ids=sorted(actuator_output_ids),
                    )
                )
                continue
            permit = rule.get("permitWhen") or {}
            effect = (
                str(rule["classification"]),
                str(rule.get("action") or ""),
                str(permit.get("signalId") or ""),
                str(permit.get("state") or ""),
                str(output),
            )
            owner = safety_effect_owners.get(effect)
            if owner is None:
                safety_effect_owners[effect] = str(rule["ruleId"])
            else:
                diagnostics.append(
                    _diagnostic(
                        "duplicate_safety_rule_effect",
                        f"/safetyRules/{index}/affectedOutputSignalIds/{position}",
                        (
                            f"Safety rule {rule['ruleId']!r} duplicates {owner!r} for "
                            f"output {output!r}; consolidate the effect into one rule."
                        ),
                        received={
                            "ruleId": rule["ruleId"],
                            "duplicateOf": owner,
                            "outputSignalId": output,
                        },
                        expected_type="one safety rule per identical permit/output effect",
                        related_ids=[owner, str(rule["ruleId"])],
                    )
                )

    alarm_latch_ids = {str(item["latchSignalId"]) for item in design["alarms"]}
    for output, rules in safety_rules_by_output.items():
        safe_latch_permits = {
            str((rule.get("permitWhen") or {}).get("signalId"))
            for rule in rules
            if (rule.get("permitWhen") or {}).get("state") == "off"
            and str((rule.get("permitWhen") or {}).get("signalId")) in alarm_latch_ids
        }
        for rule in rules:
            permit = rule.get("permitWhen") or {}
            permit_signal = str(permit.get("signalId") or "")
            if permit.get("state") != "on" or permit_signal not in safe_latch_permits:
                continue
            diagnostics.append(
                _diagnostic(
                    "contradictory_safety_boundaries_for_output",
                    "/safetyRules",
                    (
                        f"Safety rule {rule['ruleId']!r} permits output {output!r} only "
                        f"while alarm latch {permit_signal!r} is ON, contradicting the "
                        "matching latch-OFF safety boundary. Remove this ON rule and "
                        "preserve the latch-OFF rule."
                    ),
                    received={
                        "unsafeRuleId": rule["ruleId"],
                        "outputSignalId": output,
                        "permitWhen": permit,
                    },
                    expected_type="alarm latch OFF safety permit",
                    related_ids=[str(rule["ruleId"])],
                )
            )

    modes_by_id = {item["modeId"]: item for item in design["operatingModes"]}
    alarms_by_latch: dict[str, list[dict[str, Any]]] = {}
    for alarm in design["alarms"]:
        alarms_by_latch.setdefault(alarm["latchSignalId"], []).append(alarm)
    recoveries_by_latch: dict[str, list[dict[str, Any]]] = {}
    alarms_by_id = {item["alarmId"]: item for item in design["alarms"]}
    for recovery in design["recoveryRules"]:
        for alarm_id in recovery["resetAlarmIds"]:
            alarm = alarms_by_id.get(alarm_id)
            if alarm is not None:
                recoveries_by_latch.setdefault(alarm["latchSignalId"], []).append(recovery)

    def alarm_trigger_requirements(alarm: Mapping[str, Any]) -> list[tuple[str, str, str]]:
        requirements = [semantic_requirement(item) for item in alarm["triggerAll"]]
        mode = modes_by_id.get(alarm.get("modeId"))
        if mode is not None:
            activation = mode["activation"]
            requirements.append(("signal", activation["signalId"], activation["state"]))
        if alarm.get("stepId") is not None:
            requirements.append(("step", alarm["stepId"], "on"))
        return requirements

    for index, rung in enumerate(design["ladderRungs"]):
        condition(rung["condition"], f"/ladderRungs/{index}/condition")
        action = rung["action"]
        if action["instruction"] in {"MOV", "INCP"}:
            continue
        target_type = str(action.get("targetType"))
        require(
            action.get("targetId"),
            sources.get(target_type, set()),
            f"/ladderRungs/{index}/action/targetId",
            target_type,
        )
        if target_type != "signal" or action["instruction"] not in {"SET", "RST"}:
            continue
        target_id = str(action.get("targetId"))
        if action["instruction"] == "SET":
            owners = alarms_by_latch.get(target_id, [])
            if not owners:
                diagnostics.append(
                    _diagnostic(
                        "unreviewed_signal_set",
                        f"/ladderRungs/{index}/action",
                        (
                            f"Rung {rung['rungId']!r} SETs ordinary signal {target_id!r}. "
                            "SET on a signal is reserved for a declared alarm latch; use "
                            "one OUT rung and one control_network for ordinary state."
                        ),
                        received={"rungId": rung["rungId"], "signalId": target_id},
                        expected_type="declared alarm-latch SET",
                        related_ids=[item["alarmId"] for item in design["alarms"]],
                    )
                )
            elif not any(
                all(
                    guarantees(rung["condition"], required)
                    for required in alarm_trigger_requirements(alarm)
                )
                for alarm in owners
            ):
                diagnostics.append(
                    _diagnostic(
                        "alarm_set_missing_trigger_conditions",
                        f"/ladderRungs/{index}/condition",
                        (
                            f"Rung {rung['rungId']!r} SETs alarm latch {target_id!r} "
                            "without guaranteeing a declared alarm's complete trigger."
                        ),
                        received={"rungId": rung["rungId"], "signalId": target_id},
                        expected_type="condition guaranteeing one owning alarm trigger",
                        related_ids=[alarm["alarmId"] for alarm in owners],
                    )
                )
        else:
            owners = recoveries_by_latch.get(target_id, [])
            if not owners:
                diagnostics.append(
                    _diagnostic(
                        "unreviewed_signal_reset",
                        f"/ladderRungs/{index}/action",
                        (
                            f"Rung {rung['rungId']!r} RSTs ordinary or unowned signal "
                            f"{target_id!r}. RST on a signal is reserved for an alarm latch "
                            "owned by a declared recovery rule; use OUT/control_network for "
                            "ordinary state."
                        ),
                        received={"rungId": rung["rungId"], "signalId": target_id},
                        expected_type="recovery-owned alarm-latch RST",
                        related_ids=[item["recoveryId"] for item in design["recoveryRules"]],
                    )
                )
            elif not any(
                all(
                    guarantees(
                        rung["condition"],
                        semantic_requirement(item),
                    )
                    for item in recovery["whenAll"]
                )
                for recovery in owners
            ):
                diagnostics.append(
                    _diagnostic(
                        "alarm_reset_missing_recovery_conditions",
                        f"/ladderRungs/{index}/condition",
                        (
                            f"Rung {rung['rungId']!r} RSTs alarm latch {target_id!r} "
                            "without guaranteeing a declared recovery rule's whenAll."
                        ),
                        received={"rungId": rung["rungId"], "signalId": target_id},
                        expected_type="condition guaranteeing one owning recovery rule",
                        related_ids=[recovery["recoveryId"] for recovery in owners],
                    )
                )

    for timer_index, timer in enumerate(design["timers"]):
        timer_id = str(timer["timerId"])
        ton_rungs = [
            rung
            for rung in design["ladderRungs"]
            if rung["action"].get("instruction") == "TON"
            and rung["action"].get("targetType") == "timer"
            and rung["action"].get("targetId") == timer_id
        ]
        if len(ton_rungs) != 1:
            diagnostics.append(
                _diagnostic(
                    "timer_ton_rung_cardinality",
                    "/ladderRungs",
                    (
                        f"Timer {timer_id!r} requires exactly one TON rung; found "
                        f"{len(ton_rungs)}. Keep or create one rung and remove only "
                        "the duplicate TON rungs listed here."
                    ),
                    received={
                        "timerId": timer_id,
                        "tonRungIds": [str(rung["rungId"]) for rung in ton_rungs],
                    },
                    expected_type="exactly one TON rung",
                    related_ids=[str(rung["rungId"]) for rung in ton_rungs],
                )
            )

        elif not all(
            guarantees(ton_rungs[0]["condition"], semantic_requirement(item))
            for item in timer["enableAll"]
        ):
            diagnostics.append(
                _diagnostic(
                    "timer_ton_rung_missing_enable_condition",
                    f"/ladderRungs/{design['ladderRungs'].index(ton_rungs[0])}/condition",
                    (
                        f"TON rung {ton_rungs[0]['rungId']!r} for timer {timer_id!r} "
                        "must guarantee every declared timer enable condition."
                    ),
                    received={
                        "rungId": ton_rungs[0]["rungId"],
                        "timerId": timer_id,
                    },
                    expected_type="TON condition guaranteeing timer enableAll",
                    related_ids=[str(ton_rungs[0]["rungId"])],
                )
            )

    for alarm_index, alarm in enumerate(design["alarms"]):
        latch_id = str(alarm["latchSignalId"])
        requirements = alarm_trigger_requirements(alarm)
        matching_set_rungs = [
            rung
            for rung in design["ladderRungs"]
            if rung["action"].get("instruction") == "SET"
            and rung["action"].get("targetType") == "signal"
            and rung["action"].get("targetId") == latch_id
            and all(guarantees(rung["condition"], required) for required in requirements)
        ]
        if matching_set_rungs:
            continue
        diagnostics.append(
            _diagnostic(
                "alarm_missing_latch_set_rung",
                "/ladderRungs",
                (
                    f"Alarm {alarm['alarmId']!r} requires one SET rung targeting latch "
                    f"{latch_id!r} and guaranteeing its complete trigger. Add only the "
                    "missing alarm-specific rung; preserve existing alarm rungs."
                ),
                received={
                    "alarmId": alarm["alarmId"],
                    "latchSignalId": latch_id,
                    "requiredContacts": requirements,
                },
                expected_type="alarm-specific latch SET rung",
                related_ids=[
                    str(alarm["alarmId"]),
                    latch_id,
                    *[str(item[1]) for item in requirements],
                ],
            )
        )

    def matching_action_rungs(
        instruction: str,
        target_type: str,
        target_id: str,
        requirements: Sequence[tuple[str, str, str]],
    ) -> list[str]:
        return [
            rung["rungId"]
            for rung in design["ladderRungs"]
            if rung["action"].get("instruction") == instruction
            and rung["action"].get("targetType") == target_type
            and rung["action"].get("targetId") == target_id
            and all(guarantees(rung["condition"], requirement) for requirement in requirements)
        ]

    def closest_action_rungs(
        instruction: str,
        target_type: str,
        target_id: str,
        requirements: Sequence[tuple[str, str, str]],
    ) -> list[str]:
        """Name retained writers that a scoped synchronizer can safely patch.

        A missing contract does not imply a missing rung.  Returning the
        closest same-action writers keeps the small sync tools usable without
        asking the model to invent an ID or create a duplicate writer.
        """

        candidates: list[tuple[int, str]] = []
        for rung in design["ladderRungs"]:
            action = rung["action"]
            if (
                action.get("instruction") != instruction
                or action.get("targetType") != target_type
                or action.get("targetId") != target_id
            ):
                continue
            score = sum(
                guarantees(rung["condition"], requirement)
                for requirement in requirements
            )
            candidates.append((score, str(rung["rungId"])))
        if not candidates:
            return []
        best_score = max(score for score, _ in candidates)
        return [
            rung_id for score, rung_id in candidates if score == best_score
        ][:_MAX_SECTION_CHANGES]

    for recovery_index, recovery in enumerate(design["recoveryRules"]):
        requirements = [semantic_requirement(item) for item in recovery["whenAll"]]
        for sequence_id in recovery["resetSequenceIds"]:
            matching_rungs = matching_action_rungs(
                "SET",
                "sequence_reset",
                sequence_id,
                requirements,
            )
            if matching_rungs:
                continue
            candidates: list[tuple[int, str]] = []
            for rung in design["ladderRungs"]:
                action = rung["action"]
                if (
                    action.get("instruction") != "SET"
                    or action.get("targetType") != "sequence_reset"
                    or action.get("targetId") != sequence_id
                ):
                    continue
                score = sum(
                    guarantees(rung["condition"], requirement) for requirement in requirements
                )
                if score:
                    candidates.append((score, str(rung["rungId"])))
            best_score = max((score for score, _ in candidates), default=0)
            candidate_ids = [rung_id for score, rung_id in candidates if score == best_score]
            diagnostics.append(
                _diagnostic(
                    "recovery_sequence_reset_rung_missing_or_incomplete",
                    f"/recoveryRules/{recovery_index}/whenAll",
                    (
                        f"Recovery {recovery['recoveryId']!r} requires a SET "
                        f"sequence_reset rung for {sequence_id!r} that guarantees "
                        "every whenAll condition. Repair one listed candidate or add "
                        "one recovery-specific rung; preserve normal sequence "
                        "transition rungs."
                    ),
                    received={
                        "recoveryId": recovery["recoveryId"],
                        "sequenceId": sequence_id,
                        "candidateRungIds": candidate_ids,
                        "requiredContacts": requirements,
                    },
                    expected_type="SET sequence_reset rung with recovery requirements",
                    related_ids=[
                        str(recovery["recoveryId"]),
                        str(sequence_id),
                        *candidate_ids,
                        *[str(item[1]) for item in requirements],
                    ],
                )
            )

    for sequence_index, sequence in enumerate(design["sequences"]):
        steps = sequence["steps"]
        if not steps:
            continue
        start_requirements = [
            ("sequence_reset", sequence["sequenceId"], "on"),
            *(semantic_requirement(item) for item in sequence["startAll"]),
        ]
        mode = modes_by_id.get(sequence.get("modeId"))
        if mode is not None:
            activation = mode["activation"]
            start_requirements.append(("signal", activation["signalId"], activation["state"]))
        first_step_id = steps[0]["stepId"]
        start_rungs = matching_action_rungs(
            "SET",
            "step",
            first_step_id,
            start_requirements,
        )
        if not start_rungs:
            candidate_ids = closest_action_rungs(
                "SET", "step", first_step_id, start_requirements
            )
            diagnostics.append(
                _diagnostic(
                    "sequence_start_rung_missing_or_incomplete",
                    f"/sequences/{sequence_index}/startAll",
                    (
                        f"Sequence {sequence['sequenceId']!r} requires a SET step rung "
                        f"for first step {first_step_id!r} that guarantees "
                        f"{start_requirements!r}."
                    ),
                    received={
                        "sequenceId": sequence["sequenceId"],
                        "targetStepId": first_step_id,
                        "candidateRungIds": candidate_ids,
                        "requiredContacts": start_requirements,
                    },
                    expected_type="SET first-step rung with sequence start requirements",
                    related_ids=[sequence["sequenceId"], first_step_id, *candidate_ids],
                )
            )
        for step_index, step in enumerate(steps):
            requirements = [
                ("step", step["stepId"], "on"),
                *(semantic_requirement(item) for item in step["completeAll"]),
            ]
            if mode is not None:
                activation = mode["activation"]
                requirements.append(("signal", activation["signalId"], activation["state"]))
            if step_index + 1 < len(steps):
                target_type = "step"
                target_id = steps[step_index + 1]["stepId"]
            else:
                target_type = "sequence_reset"
                target_id = sequence["sequenceId"]
            transition_rungs = matching_action_rungs(
                "SET",
                target_type,
                target_id,
                requirements,
            )
            if not transition_rungs:
                diagnostics.append(
                    _diagnostic(
                        "sequence_transition_rung_missing_or_incomplete",
                        f"/sequences/{sequence_index}/steps/{step_index}/completeAll",
                        (
                            f"Step {step['stepId']!r} requires a SET {target_type} rung "
                            f"for {target_id!r} that guarantees {requirements!r}."
                        ),
                        received={
                            "sequenceId": sequence["sequenceId"],
                            "sourceStepId": step["stepId"],
                            "targetType": target_type,
                            "targetId": target_id,
                        },
                        expected_type="SET sequence transition rung with completion requirements",
                        related_ids=[sequence["sequenceId"], step["stepId"], target_id],
                    )
                )
        reset_requirements = [semantic_requirement(item) for item in sequence["resetAll"]]
        reset_rungs = matching_action_rungs(
            "SET",
            "sequence_reset",
            sequence["sequenceId"],
            reset_requirements,
        )
        if not reset_rungs:
            candidate_ids = closest_action_rungs(
                "SET", "sequence_reset", sequence["sequenceId"], reset_requirements
            )
            diagnostics.append(
                _diagnostic(
                    "sequence_reset_rung_missing_or_incomplete",
                    f"/sequences/{sequence_index}/resetAll",
                    (
                        f"Sequence {sequence['sequenceId']!r} requires a SET "
                        "sequence_reset rung that guarantees every resetAll "
                        f"condition: {reset_requirements!r}. Compare the resetAll declaration "
                        "and existing rung with the user's requested reset behavior. If the "
                        "declaration is wrong, correct it with design.edit_sequences; otherwise "
                        "edit or add the reset rung. Do not copy a mistaken declaration into "
                        "the program merely to clear this diagnostic. Preserve unrelated "
                        "completion and recovery rungs."
                    ),
                    received={
                        "sequenceId": sequence["sequenceId"],
                        "candidateRungIds": candidate_ids,
                        "requiredContacts": reset_requirements,
                    },
                    expected_type="SET sequence_reset rung with reset requirements",
                    related_ids=[
                        str(sequence["sequenceId"]),
                        *candidate_ids,
                        *[str(item[1]) for item in reset_requirements],
                    ],
                )
            )
    for rung_index, increment in enumerate(design["ladderRungs"]):
        action = increment["action"]
        if action.get("instruction") != "INCP":
            continue
        increment_steps = positive_step_contacts(increment["condition"])
        consuming_resets = [
            rung
            for rung in design["ladderRungs"][:rung_index]
            if rung["action"].get("instruction") == "SET"
            and rung["action"].get("targetType") == "sequence_reset"
            and increment_steps.intersection(positive_step_contacts(rung["condition"]))
        ]
        if consuming_resets:
            diagnostics.append(
                _diagnostic(
                    "sequence_completion_consumed_before_data_observer",
                    f"/ladderRungs/{rung_index}",
                    (
                        f"INCP rung {increment['rungId']!r} reads a completion step "
                        "after an earlier sequence-reset rung consumes that step in the "
                        "same PLC scan. Move the INCP rung before the consuming reset."
                    ),
                    received={
                        "observerRungId": increment["rungId"],
                        "observedStepIds": sorted(increment_steps),
                        "earlierResetRungIds": [
                            rung["rungId"] for rung in consuming_resets
                        ],
                    },
                    expected_type="data observer ordered before sequence completion reset",
                    related_ids=[
                        increment["rungId"],
                        *[rung["rungId"] for rung in consuming_resets],
                    ],
                )
            )
        address = action["destinationAddress"]
        resets = [
            rung
            for rung in design["ladderRungs"]
            if rung["action"].get("instruction") == "MOV"
            and rung["action"].get("destinationAddress") == address
            and rung["action"].get("sourceConstant") == 0
        ]
        if len(resets) != 1:
            diagnostics.append(
                _diagnostic(
                    "increment_reset_writer_mismatch",
                    f"/ladderRungs/{rung_index}/action/destinationAddress",
                    (
                        f"INCP rung {increment['rungId']!r} for {address!r} requires "
                        "exactly one MOV 0 reset rung for the same register."
                    ),
                    received={
                        "incrementRungId": increment["rungId"],
                        "destinationAddress": address,
                        "resetRungIds": [rung["rungId"] for rung in resets],
                    },
                    expected_type="one MOV 0 reset writer",
                    related_ids=[increment["rungId"], *[rung["rungId"] for rung in resets]],
                )
            )
            continue
        increment_contacts = guaranteed_contacts(increment["condition"])
        reset_contacts = guaranteed_contacts(resets[0]["condition"])
        opposite_pairs = [
            (source_type, source_id, state)
            for source_type, source_id, state in increment_contacts
            if (
                source_type,
                source_id,
                "off" if state == "on" else "on",
            )
            in reset_contacts
        ]
        if not opposite_pairs:
            diagnostics.append(
                _diagnostic(
                    "increment_reset_not_mutually_exclusive",
                    f"/ladderRungs/{rung_index}/condition",
                    (
                        f"INCP rung {increment['rungId']!r} and MOV 0 reset rung "
                        f"{resets[0]['rungId']!r} for {address!r} must guarantee "
                        "opposite states of at least one shared contact."
                    ),
                    received={
                        "incrementRungId": increment["rungId"],
                        "incrementContacts": sorted(increment_contacts),
                        "resetRungId": resets[0]["rungId"],
                        "resetContacts": sorted(reset_contacts),
                    },
                    expected_type="mutually exclusive INCP and MOV 0 conditions",
                    related_ids=[increment["rungId"], resets[0]["rungId"]],
                )
            )
    networks_by_output: dict[str, list[str]] = {}
    for network in design["controlNetworks"]:
        networks_by_output.setdefault(network["outputSignalId"], []).append(network["networkId"])
    out_rungs_by_output: dict[str, list[str]] = {}
    for rung in design["ladderRungs"]:
        if (
            rung["action"].get("instruction") == "OUT"
            and rung["action"].get("targetType") == "signal"
        ):
            out_rungs_by_output.setdefault(rung["action"]["targetId"], []).append(rung["rungId"])
    for output_id in sorted(set(networks_by_output) | set(out_rungs_by_output)):
        network_ids = networks_by_output.get(output_id, [])
        rung_ids = out_rungs_by_output.get(output_id, [])
        if len(network_ids) != 1 or len(rung_ids) != 1:
            diagnostics.append(
                _diagnostic(
                    "control_network_out_cardinality_mismatch",
                    "/controlNetworks",
                    (
                        f"Writable output {output_id!r} requires exactly one control_network "
                        f"and exactly one OUT rung; found {len(network_ids)} network(s) and "
                        f"{len(rung_ids)} OUT rung(s)."
                    ),
                    received={
                        "outputSignalId": output_id,
                        "networkIds": network_ids,
                        "outRungIds": rung_ids,
                    },
                    expected_type="one control network paired with one OUT rung",
                    related_ids=[*network_ids, *rung_ids],
                )
            )
    for index, network in enumerate(design["controlNetworks"]):
        output_id = network["outputSignalId"]
        requirements = [semantic_requirement(item) for item in network["permitAll"]]
        if network.get("modeId") in modes_by_id:
            activation = modes_by_id[network["modeId"]]["activation"]
            requirements.append(("signal", activation["signalId"], activation["state"]))
        if network.get("stepId") is not None:
            requirements.append(("step", network["stepId"], "on"))
        safety_rule_ids: list[str] = []
        for rule in design["safetyRules"]:
            if (
                rule["classification"] == "process_interlock"
                and output_id in rule["affectedOutputSignalIds"]
                and isinstance(rule.get("permitWhen"), dict)
            ):
                permit = rule["permitWhen"]
                requirements.append(semantic_requirement(permit))
                safety_rule_ids.append(rule["ruleId"])
        candidate_rungs = [
            rung
            for rung in design["ladderRungs"]
            if rung["action"].get("instruction") == "OUT"
            and rung["action"].get("targetType") == "signal"
            and rung["action"].get("targetId") == output_id
        ]
        if candidate_rungs and not any(
            all(guarantees(rung["condition"], required) for required in requirements)
            for rung in candidate_rungs
        ):
            diagnostics.append(
                _diagnostic(
                    "control_network_out_missing_effective_permits",
                    f"/controlNetworks/{index}/permitAll",
                    (
                        f"OUT rung for network {network['networkId']!r} and output "
                        f"{output_id!r} must guarantee every effective permit: "
                        f"{requirements!r}. Compare both the network declaration and "
                        "the actual rung with the confirmed operating requirements. "
                        "If the declaration is wrong, repair it with design.edit_control_networks; "
                        "otherwise repair the rung with design.edit_ladder_rungs. Do not copy "
                        "a mistaken declaration into working logic just to clear this diagnostic. "
                        "Preserve required safety-rule permits."
                    ),
                    received=[rung["rungId"] for rung in candidate_rungs],
                    expected_type="OUT condition containing all effective permits",
                    related_ids=[network["networkId"], *safety_rule_ids],
                )
            )
    return diagnostics[:_MAX_DIAGNOSTICS]


def _requirement_diagnostics(design: dict[str, Any], brief: ProjectBriefV1) -> list[dict[str, Any]]:
    fields = (
        "machine_purpose",
        "sequence",
        "hardware_and_io",
        "safety_and_interlocks",
        "operating_modes",
        "performance_and_acceptance",
        "supplemental_requirements",
    )
    brief_text = "\n".join(str(getattr(brief, field) or "") for field in fields).casefold()
    diagnostics: list[dict[str, Any]] = []
    modes = design["operatingModes"]
    sequences = design["sequences"]
    auto_mode_ids = {
        str(mode["modeId"])
        for mode in modes
        if any(
            marker in f"{mode['modeId']} {mode['name']}".casefold()
            for marker in ("auto", "automatic", "자동")
        )
    }
    manual_mode_ids = {
        str(mode["modeId"])
        for mode in modes
        if any(
            marker in f"{mode['modeId']} {mode['name']}".casefold() for marker in ("manual", "수동")
        )
    }

    def signal_contacts(value: Mapping[str, Any]) -> set[tuple[str, str]]:
        if value.get("kind") == "contact" and value.get("sourceType") == "signal":
            return {(str(value.get("sourceId")), str(value.get("state")))}
        contacts: set[tuple[str, str]] = set()
        for term in value.get("terms") or []:
            if isinstance(term, dict):
                contacts.update(signal_contacts(term))
        return contacts

    def typed_contacts(value: Mapping[str, Any]) -> set[tuple[str, str, str]]:
        if value.get("kind") == "contact":
            return {
                (
                    str(value.get("sourceType")),
                    str(value.get("sourceId")),
                    str(value.get("state")),
                )
            }
        contacts: set[tuple[str, str, str]] = set()
        for term in value.get("terms") or []:
            if isinstance(term, dict):
                contacts.update(typed_contacts(term))
        return contacts

    def has_path_without_target_self_on(value: Mapping[str, Any], target_id: str) -> bool:
        """Whether an OUT condition can become true while its target is still OFF."""

        kind = value.get("kind")
        if kind == "contact":
            return not (
                value.get("sourceType") == "signal"
                and value.get("sourceId") == target_id
                and value.get("state") == "on"
            )
        terms = [term for term in value.get("terms") or [] if isinstance(term, dict)]
        if kind == "any_of":
            return any(has_path_without_target_self_on(term, target_id) for term in terms)
        if kind == "all_of":
            return all(has_path_without_target_self_on(term, target_id) for term in terms)
        return True

    def condition_paths(
        value: Mapping[str, Any],
    ) -> list[set[tuple[str, str, str]]]:
        """Expand a condition into its possible true paths in contact form."""

        kind = value.get("kind")
        if kind == "contact":
            return [
                {
                    (
                        str(value.get("sourceType")),
                        str(value.get("sourceId")),
                        str(value.get("state")),
                    )
                }
            ]
        terms = [term for term in value.get("terms") or [] if isinstance(term, dict)]
        if kind == "any_of":
            return [path for term in terms for path in condition_paths(term)]
        if kind == "all_of":
            paths: list[set[tuple[str, str, str]]] = [set()]
            for term in terms:
                term_paths = condition_paths(term)
                if not term_paths:
                    return []
                paths = [left | right for left in paths for right in term_paths]
            return paths
        return []

    def independent_start_paths(
        value: Mapping[str, Any], target_id: str
    ) -> list[set[tuple[str, str, str]]]:
        """Return true paths that can energize an OUT while its target is OFF."""

        return [path for path in condition_paths(value) if ("signal", target_id, "on") not in path]

    io_ids = {str(item["signalId"]) for item in design["ioPoints"]}
    io_points_by_id = {str(item["signalId"]): item for item in design["ioPoints"]}

    def signal_text(signal_id: str) -> str:
        point = io_points_by_id.get(signal_id, {})
        return " ".join(
            str(point.get(field) or "") for field in ("signalId", "symbol", "description")
        ).casefold()

    stop_signal_ids = {
        signal_id
        for signal_id, point in io_points_by_id.items()
        if point.get("kind") == "digital_input"
        and any(marker in signal_text(signal_id) for marker in ("stop", "정지"))
        and not any(
            marker in signal_text(signal_id) for marker in ("emergency", "e_stop", "estop", "비상")
        )
    }
    if stop_signal_ids:
        mode_activation_signal_ids = {str(mode["activation"]["signalId"]) for mode in modes}
        stop_governed_output_ids = {
            signal_id
            for signal_id, point in io_points_by_id.items()
            if point.get("kind") == "digital_output"
            or any(
                marker in signal_text(signal_id)
                for marker in (
                    "move_command",
                    "move_active",
                    "moving",
                    "system_running",
                    "run_state",
                    "이동 명령",
                    "이동 동작",
                    "동작 중",
                    "운전 중",
                )
            )
        }
        stop_governed_output_ids.update(mode_activation_signal_ids)
        missing_stop_rungs: list[dict[str, Any]] = []
        for rung_index, rung in enumerate(design["ladderRungs"]):
            action = rung["action"]
            if (
                action.get("instruction") != "OUT"
                or action.get("targetType") != "signal"
                or str(action.get("targetId") or "") not in stop_governed_output_ids
            ):
                continue
            missing_by_path = [
                sorted(
                    stop_signal_id
                    for stop_signal_id in stop_signal_ids
                    if ("signal", stop_signal_id, "off") not in path
                )
                for path in condition_paths(rung["condition"])
            ]
            missing_by_path = [item for item in missing_by_path if item]
            if missing_by_path:
                target_id = str(action.get("targetId") or "")
                missing_stop_rungs.append(
                    {
                        "rungIndex": rung_index,
                        "rungId": str(rung["rungId"]),
                        "targetId": target_id,
                        "missingByPath": missing_by_path,
                    }
                )
        if missing_stop_rungs:
            diagnostics.append(
                _diagnostic(
                    "stop_interlock_missing_from_output_paths",
                    "/ladderRungs",
                    (
                        "Every true path for the listed stop-governed outputs must require the declared "
                        "operator stop input OFF. Patch all candidate rungs in one edit by putting the stop "
                        "OFF contact outside each start/self-hold branch. Pressing stop must drop run, mode, "
                        "and movement states and permit a later mode selection while stopped."
                    ),
                    received={"candidateRungs": missing_stop_rungs},
                    expected_type="every listed OUT path contains operator stop OFF",
                    related_ids=[
                        *[item["rungId"] for item in missing_stop_rungs],
                        *sorted(stop_signal_ids),
                    ],
                )
            )

    hazard_signal_ids = {
        signal_id
        for signal_id in io_ids
        if io_points_by_id.get(signal_id, {}).get("kind") == "digital_input"
        and re.search(
            r"(?:^|_)(?:emergency(?:_stop)?|e_stop|estop|door_open)(?:_|$)",
            signal_id.casefold(),
        )
    }
    hazard_normal_states = {
        str(point["signalId"]): point.get("normalState")
        for point in design["ioPoints"]
        if str(point["signalId"]) in hazard_signal_ids
    }
    for rung_index, rung in enumerate(design["ladderRungs"]):
        if rung["action"].get("targetType") != "sequence_reset":
            continue
        # A trip may legitimately reset a sequence. Inspect only rungs that
        # implement an operator recovery, not all sequence-reset actions.
        paths = condition_paths(rung["condition"])
        if not any(
            rung["action"].get("targetId") in recovery["resetSequenceIds"]
            and recovery["whenAll"]
            and paths
            and all(
                all(("signal", str(item["signalId"]), str(item["state"])) in path
                    for item in recovery["whenAll"])
                for path in paths
            )
            for recovery in design["recoveryRules"]
        ):
            continue
        active_reset_hazards = {
            signal_id: hazard_normal_states[signal_id]
            for signal_id, state in signal_contacts(rung["condition"])
            if hazard_normal_states.get(signal_id) in {"on", "off"}
            and state != hazard_normal_states[signal_id]
        }
        if active_reset_hazards:
            diagnostics.append(
                _diagnostic(
                    "recovery_requires_active_hazard",
                    f"/ladderRungs/{rung_index}/condition",
                    "Recovery must not require an active hazard. Match the declared recovery "
                    "rule and use each hazard input's normalState; do not combine independent "
                    "alarm causes as required active contacts in a shared reset rung.",
                    received=active_reset_hazards,
                    related_ids=[str(rung["rungId"])],
                )
            )
    if hazard_signal_ids:
        for network_index, network in enumerate(design["controlNetworks"]):
            output_id = str(network["outputSignalId"])
            if output_id.casefold().startswith("alarm_"):
                continue
            for permit_index, permit in enumerate(network["permitAll"]):
                signal_id = str(permit["signalId"])
                normal_state = hazard_normal_states.get(signal_id)
                if signal_id in hazard_signal_ids and permit["state"] != normal_state:
                    diagnostics.append(
                        _diagnostic(
                            "hazard_active_used_as_run_permit",
                            f"/controlNetworks/{network_index}/permitAll/{permit_index}/state",
                            (
                                f"Hazard input '{signal_id}' has declared normalState={normal_state!r}. "
                                "Use its declared normal state as the run permit; NC and NO inputs "
                                "have different polarities. If normalState is unknown, establish it "
                                "from the confirmed requirements before enabling an output."
                            ),
                            received=permit,
                            expected_type=f"hazard signal in declared normal state {normal_state}",
                            related_ids=[str(network["networkId"])],
                        )
                    )
        for rung_index, rung in enumerate(design["ladderRungs"]):
            action = rung["action"]
            if (
                action.get("instruction") != "OUT"
                or action.get("targetType") != "signal"
                or str(action.get("targetId") or "").casefold().startswith("alarm_")
            ):
                continue
            active_hazards = sorted(
                signal_id
                for signal_id, state in signal_contacts(rung["condition"])
                if signal_id in hazard_signal_ids and state != hazard_normal_states.get(signal_id)
            )
            if active_hazards:
                diagnostics.append(
                    _diagnostic(
                        "hazard_active_used_as_output_permit",
                        f"/ladderRungs/{rung_index}/condition",
                        "A normal output rung cannot be enabled by an active hazard. Require each listed signal in its declared normalState (NC may be ON; NO may be OFF).",
                        received={signal_id: hazard_normal_states.get(signal_id) for signal_id in active_hazards},
                        expected_type="hazard signal contacts in their declared normal states",
                        related_ids=[str(rung["rungId"])],
                    )
                )

    self_latched_alarm_ids = {
        str(alarm["alarmId"])
        for alarm in design["alarms"]
        if str(alarm.get("latchSignalId") or "") == str(alarm.get("alarmId") or "")
    }
    for network_index, network in enumerate(design["controlNetworks"]):
        output_id = str(network.get("outputSignalId") or "")
        self_permits = [
            permit
            for permit in network.get("permitAll") or []
            if str(permit.get("signalId") or "") == output_id and permit.get("state") == "on"
        ]
        if self_permits and output_id not in self_latched_alarm_ids:
            alarm_hint = (
                " For timeout indicators use alarm_latch ON at the network layer and the timer "
                "condition in the OUT rung; for emergency/door indicators use the corresponding "
                "hazard input in the state opposite its declared normalState."
                if output_id.casefold().startswith("alarm_")
                else ""
            )
            diagnostics.append(
                _diagnostic(
                    "control_network_output_self_permit",
                    f"/controlNetworks/{network_index}/permitAll",
                    (
                        "A control network cannot require its own output to already be ON. "
                        "Replace the self permit with an independent cause signal." + alarm_hint
                    ),
                    received=self_permits,
                    expected_type="independent cause signal, never output self",
                    related_ids=[str(network["networkId"])],
                )
            )

    sequence_step_ids = {
        str(step["stepId"]) for sequence in sequences for step in sequence["steps"]
    }
    for rung_index, rung in enumerate(design["ladderRungs"]):
        action = rung["action"]
        if action.get("instruction") != "OUT" or action.get("targetType") != "signal":
            continue
        target_id = str(action.get("targetId") or "")
        if target_id not in self_latched_alarm_ids and not has_path_without_target_self_on(
            rung["condition"], target_id
        ):
            diagnostics.append(
                _diagnostic(
                    "output_self_dependency_has_no_start_path",
                    f"/ladderRungs/{rung_index}/condition",
                    "An OUT rung cannot require its own target to already be ON on every energizing path. Provide an independent start path or remove the self contact.",
                    received={"rungId": rung["rungId"], "targetId": target_id},
                    expected_type="condition with an energizing path while the target is off",
                    related_ids=[str(rung["rungId"]), target_id],
                )
            )

        if any(marker in target_id.casefold() for marker in ("move_active", "moving")):
            paths = condition_paths(rung["condition"])
            non_step_paths = [
                sorted(path)
                for path in paths
                if not any(("step", step_id, "on") in path for step_id in sequence_step_ids)
            ]
            if sequence_step_ids and non_step_paths:
                diagnostics.append(
                    _diagnostic(
                        "movement_active_has_non_step_path",
                        f"/ladderRungs/{rung_index}/condition",
                        "Every path that energizes the movement-active state must include an active movement sequence step. Remove completion-only, button-only, or self-only parallel paths.",
                        received={
                            "rungId": rung["rungId"],
                            "invalidPaths": non_step_paths,
                        },
                        expected_type="every true path contains a movement step contact in state on",
                        related_ids=[str(rung["rungId"]), *sorted(sequence_step_ids)],
                    )
                )

    for network_index, network in enumerate(design["controlNetworks"]):
        output_id = str(network.get("outputSignalId") or "")
        if not any(marker in output_id.casefold() for marker in ("move_active", "moving")):
            continue
        completion_permits = [
            permit
            for permit in network.get("permitAll") or []
            if any(
                marker in str(permit.get("signalId") or "").casefold()
                for marker in ("move_complete", "complete", "완료")
            )
        ]
        if completion_permits:
            diagnostics.append(
                _diagnostic(
                    "movement_active_network_uses_completion_permit",
                    f"/controlNetworks/{network_index}/permitAll",
                    (
                        "A movement-active control network cannot use movement completion as its permit. "
                        "Replace it with shared safety permits such as emergency-stop OFF, door-open OFF, "
                        "and alarm-latch OFF when those signals exist; the OUT rung will then be checked "
                        "against those common safety permits."
                    ),
                    received=completion_permits,
                    expected_type="shared safety permits, never movement completion",
                    related_ids=[str(network["networkId"])],
                )
            )

    if (
        "자동" in brief_text
        and any(marker in brief_text for marker in ("반복", "계속"))
        and not any(
            sequence.get("modeId") in auto_mode_ids
            and sequence.get("restartPolicy") == "continuous"
            for sequence in sequences
        )
    ):
        diagnostics.append(
            _diagnostic(
                "automatic_mode_requires_continuous_sequence",
                "/sequences",
                "Confirmed automatic repetition needs a continuous sequence bound to the automatic mode.",
                related_ids=sorted(auto_mode_ids),
            )
        )

    if "수동" in brief_text and any(
        marker in brief_text for marker in ("한 번", "1회", "놓아야", "릴리즈")
    ):
        if not any(
            sequence.get("modeId") in manual_mode_ids
            and sequence.get("restartPolicy") == "require_start_release"
            for sequence in sequences
        ):
            diagnostics.append(
                _diagnostic(
                    "manual_mode_requires_release_sequence",
                    "/sequences",
                    "Confirmed one-move-per-press manual operation needs a require_start_release sequence bound to the manual mode.",
                    related_ids=sorted(manual_mode_ids),
                )
            )

        start_signal_ids = {
            signal_id
            for signal_id in io_ids
            if io_points_by_id.get(signal_id, {}).get("kind") == "digital_input"
            and any(marker in signal_id.casefold() for marker in (
                "start", "시작", "manual_move_btn", "manual_move_button", "jog_btn", "jog_button",
            ))
        }
        for sequence_index, sequence in enumerate(sequences):
            if sequence.get("modeId") not in manual_mode_ids:
                continue
            start_conditions = {
                (str(item["signalId"]), str(item["state"])) for item in sequence["startAll"]
            }
            first_step = next(iter(sequence.get("steps", [])), {})
            idle_step_id = first_step.get("stepId")
            idle_waits_for_command = any(
                item.get("signalId") in start_signal_ids and item.get("state") == "on"
                for item in first_step.get("completeAll", [])
            )
            # Entering an inert waiting step is not commanding motion. Do not
            # force the operator button into startAll when the first transition
            # already requires it. Conservatively reject any signal writer
            # depending on that step, including indirect internal permits.
            idle_drives_signal = any(
                rung.get("action", {}).get("targetType") == "signal"
                and any(
                    source_type == "step" and source_id == idle_step_id
                    for path in condition_paths(rung["condition"])
                    for source_type, source_id, _state in path
                )
                for rung in design["ladderRungs"]
            ) or any(
                network.get("stepId") == idle_step_id
                for network in design["controlNetworks"]
            ) if idle_step_id else True
            starts_in_inert_wait = idle_waits_for_command and not idle_drives_signal
            if start_signal_ids and not any(
                (signal_id, "on") in start_conditions for signal_id in start_signal_ids
            ) and not starts_in_inert_wait:
                diagnostics.append(
                    _diagnostic(
                        "manual_sequence_missing_operator_start",
                        f"/sequences/{sequence_index}/startAll",
                        "Manual one-move operation must start from an explicit operator start input; mode selection alone must not command motion.",
                        expected_type="operator start signal in state on",
                        related_ids=sorted(start_signal_ids),
                    )
                )

            start_ids = {
                signal_id
                for signal_id in io_ids
                if "start" in signal_text(signal_id) or "시작" in signal_text(signal_id)
            }
            completion_ids = {
                signal_id
                for signal_id in io_ids
                if any(
                    marker in signal_text(signal_id)
                    for marker in ("move_complete", "complete", "이동 완료")
                )
            }
            combined_completion_release_steps = []
            for step in sequence["steps"]:
                completion_conditions = {
                    (str(item["signalId"]), str(item["state"])) for item in step["completeAll"]
                }
                if any(
                    (signal_id, "on") in completion_conditions for signal_id in completion_ids
                ) and any((signal_id, "off") in completion_conditions for signal_id in start_ids):
                    combined_completion_release_steps.append(str(step["stepId"]))
            if combined_completion_release_steps:
                diagnostics.append(
                    _diagnostic(
                        "manual_completion_must_not_wait_for_button_release",
                        f"/sequences/{sequence_index}/steps",
                        (
                            "A manual movement step cannot require completion ON and start-button OFF at the "
                            "same instant. A short completion pulse can occur while the operator still holds "
                            "start and would be lost. Complete the movement step from completion ON, transition "
                            "to a separate wait-for-release step, and re-arm only after start OFF."
                        ),
                        received=combined_completion_release_steps,
                        expected_type="movement-complete step followed by a separate button-release step",
                        related_ids=[
                            str(sequence["sequenceId"]),
                            *combined_completion_release_steps,
                        ],
                    )
                )

            alarm_latch_ids = {str(alarm["latchSignalId"]) for alarm in design["alarms"]}
            initial_arm_rungs = [
                rung
                for rung in design["ladderRungs"]
                if rung["action"].get("instruction") == "SET"
                and rung["action"].get("targetType") == "sequence_reset"
                and rung["action"].get("targetId") == sequence["sequenceId"]
                and any(
                    not any(
                        (source_type == "step" and state == "on")
                        or (
                            source_type == "signal"
                            and source_id in alarm_latch_ids
                            and state == "on"
                        )
                        for source_type, source_id, state in path
                    )
                    for path in condition_paths(rung["condition"])
                )
            ]
            if not initial_arm_rungs:
                diagnostics.append(
                    _diagnostic(
                        "manual_sequence_missing_idle_initial_arm",
                        "/ladderRungs",
                        (
                            "The manual sequence has no normal idle path that initializes/re-arms its reset "
                            "step without a prior active step or alarm. Add an idle sequence_reset SET rung "
                            "using start OFF and completion OFF; recovery reset remains a separate path."
                        ),
                        expected_type="normal idle sequence_reset SET path",
                        related_ids=[str(sequence["sequenceId"])],
                    )
                )

    if "준비" in brief_text and auto_mode_ids:
        ready_signal_ids = {
            signal_id
            for signal_id in io_ids
            if "ready" in signal_id.casefold() or "준비" in signal_id
        }
        for sequence_index, sequence in enumerate(sequences):
            if sequence.get("modeId") not in auto_mode_ids:
                continue
            start_conditions = {
                (str(item["signalId"]), str(item["state"])) for item in sequence["startAll"]
            }
            if ready_signal_ids and not any(
                (signal_id, "on") in start_conditions for signal_id in ready_signal_ids
            ):
                diagnostics.append(
                    _diagnostic(
                        "automatic_sequence_missing_readiness",
                        f"/sequences/{sequence_index}/startAll",
                        "The confirmed automatic sequence must check the simulated readiness input before starting a move.",
                        expected_type="readiness signal in state on",
                        related_ids=sorted(ready_signal_ids),
                    )
                )

    continuous_auto_sequences = [
        sequence
        for sequence in sequences
        if sequence.get("modeId") in auto_mode_ids and sequence.get("restartPolicy") == "continuous"
    ]
    if continuous_auto_sequences and any(
        marker in brief_text for marker in ("한 번 시작", "계속 반복", "반복")
    ):
        start_signal_ids = {
            signal_id
            for signal_id in io_ids
            if io_points_by_id.get(signal_id, {}).get("kind") == "digital_input"
            and ("start" in signal_text(signal_id) or "시작" in signal_text(signal_id))
        }
        running_signal_ids = set()
        for signal_id in io_ids:
            point = io_points_by_id.get(signal_id, {})
            identifier_text = " ".join(
                str(point.get(field) or "") for field in ("signalId", "symbol")
            ).casefold()
            description_text = str(point.get("description") or "").casefold()
            if (
                any(marker in identifier_text for marker in ("system_running", "run_state"))
                or "시스템 동작 중" in description_text
                or "시스템 운전 중" in description_text
            ):
                running_signal_ids.add(signal_id)
        for sequence_index, sequence in enumerate(sequences):
            if sequence not in continuous_auto_sequences:
                continue
            start_conditions = {
                (str(item["signalId"]), str(item["state"])) for item in sequence["startAll"]
            }
            raw_start_ids = sorted(
                signal_id for signal_id in start_signal_ids if (signal_id, "on") in start_conditions
            )
            has_latched_run = any(
                (signal_id, "on") in start_conditions for signal_id in running_signal_ids
            )
            if raw_start_ids or (running_signal_ids and not has_latched_run):
                diagnostics.append(
                    _diagnostic(
                        "continuous_sequence_start_contract_uses_momentary_button",
                        f"/sequences/{sequence_index}/startAll",
                        (
                            "A continuous automatic sequence's declared startAll contract must use the "
                            "latched run-state ON, not the momentary start button. Remove the raw start "
                            "condition, add the run-state ON condition, and preserve its automatic-mode "
                            "and readiness requirements. The operator start belongs only in the run latch."
                        ),
                        received={
                            "rawStartSignalIds": raw_start_ids,
                            "hasLatchedRun": has_latched_run,
                        },
                        expected_type="continuous sequence start contract gated by latched run state",
                        related_ids=[
                            str(sequence["sequenceId"]),
                            *sorted(running_signal_ids),
                            *raw_start_ids,
                        ],
                    )
                )
        for running_signal_id in sorted(running_signal_ids):
            running_rungs = [
                (index, rung)
                for index, rung in enumerate(design["ladderRungs"])
                if rung["action"].get("instruction") == "OUT"
                and rung["action"].get("targetType") == "signal"
                and rung["action"].get("targetId") == running_signal_id
            ]
            for rung_index, rung in running_rungs:
                paths = condition_paths(rung["condition"])
                has_hold_path = any(("signal", running_signal_id, "on") in path for path in paths)
                has_start_path = any(
                    any(("signal", signal_id, "on") in path for signal_id in start_signal_ids)
                    and ("signal", running_signal_id, "on") not in path
                    for path in paths
                )
                if not has_hold_path or not has_start_path:
                    diagnostics.append(
                        _diagnostic(
                            "continuous_auto_requires_latched_run_state",
                            f"/ladderRungs/{rung_index}/condition",
                            (
                                "Confirmed automatic operation must continue after one start command. The run "
                                "state needs an independent start-button path plus a self-hold path, with stop "
                                "and safety contacts common to both paths. Requiring start ON on every path "
                                "stops automatic cycling as soon as the button is released."
                            ),
                            received={
                                "rungId": rung["rungId"],
                                "hasStartPath": has_start_path,
                                "hasHoldPath": has_hold_path,
                            },
                            expected_type="latched run OUT with independent start and self-hold paths",
                            related_ids=[
                                str(rung["rungId"]),
                                running_signal_id,
                                *sorted(start_signal_ids),
                            ],
                        )
                    )

        for sequence_index, sequence in enumerate(sequences):
            if sequence not in continuous_auto_sequences or not sequence["steps"]:
                continue
            first_step_id = str(sequence["steps"][0]["stepId"])
            first_step_rungs = [
                (index, rung)
                for index, rung in enumerate(design["ladderRungs"])
                if rung["action"].get("instruction") == "SET"
                and rung["action"].get("targetType") == "step"
                and rung["action"].get("targetId") == first_step_id
            ]
            for rung_index, rung in first_step_rungs:
                contacts = typed_contacts(rung["condition"])
                if start_signal_ids and any(
                    ("signal", signal_id, "on") in contacts for signal_id in start_signal_ids
                ):
                    diagnostics.append(
                        _diagnostic(
                            "continuous_sequence_cycle_depends_on_start_button",
                            f"/ladderRungs/{rung_index}/condition",
                            (
                                "The first step of a continuous automatic sequence cannot require the momentary "
                                "start button on every cycle. Gate it from the latched run-state signal and mode; "
                                "the operator start button belongs only in the run-latch start branch."
                            ),
                            received={
                                "rungId": rung["rungId"],
                                "sequenceId": sequence["sequenceId"],
                            },
                            expected_type="first-step SET gated by latched run state, not raw start",
                            related_ids=[
                                str(rung["rungId"]),
                                str(sequence["sequenceId"]),
                                *sorted(running_signal_ids),
                            ],
                        )
                    )

    alarm_outputs = {
        str(alarm["alarmId"]): alarm
        for alarm in design["alarms"]
        if str(alarm["alarmId"]) in io_ids
    }
    latch_owners: dict[str, list[str]] = {}
    for alarm_id, alarm in alarm_outputs.items():
        latch_owners.setdefault(str(alarm["latchSignalId"]), []).append(alarm_id)
    for latch_id, alarm_ids in latch_owners.items():
        if len(alarm_ids) > 1:
            diagnostics.append(
                _diagnostic(
                    "alarm_indicators_require_distinct_cause_latches",
                    "/alarms",
                    (
                        f"Alarm indicators {sorted(alarm_ids)!r} share latch {latch_id!r}. After a cause clears, "
                        "one shared latch cannot retain which alarm occurred. Give each alarm a distinct internal "
                        "latch, SET it from that alarm's trigger, reset all of them through the recovery rule, "
                        "and interlock movement from every alarm latch OFF."
                    ),
                    received={"latchSignalId": latch_id, "alarmIds": sorted(alarm_ids)},
                    expected_type="one persistent latch per human-readable alarm cause",
                    related_ids=[latch_id, *sorted(alarm_ids)],
                )
            )

    for alarm_id, alarm in alarm_outputs.items():
        latch_id = str(alarm["latchSignalId"])
        trigger_ids = {str(item["signalId"]) for item in alarm["triggerAll"]}
        for rung_index, rung in enumerate(design["ladderRungs"]):
            action = rung["action"]
            if (
                action.get("instruction") != "OUT"
                or action.get("targetType") != "signal"
                or action.get("targetId") != alarm_id
            ):
                continue
            paths = condition_paths(rung["condition"])
            missing_latch = any(("signal", latch_id, "on") not in path for path in paths)
            transient_dependencies = sorted(
                {
                    source_id
                    for path in paths
                    for _source_type, source_id, state in path
                    if source_id in trigger_ids and source_id != latch_id and state == "on"
                }
            )
            if missing_latch or transient_dependencies:
                diagnostics.append(
                    _diagnostic(
                        "alarm_indicator_not_persistent_until_reset",
                        f"/ladderRungs/{rung_index}/condition",
                        (
                            f"Alarm indicator {alarm_id!r} must remain visible from its dedicated latch "
                            f"{latch_id!r} until operator reset. Its OUT condition must require that latch ON "
                            "and must not also require a transient timer/hazard trigger that disappears after "
                            "the latch stops motion."
                        ),
                        received={
                            "rungId": rung["rungId"],
                            "missingLatch": missing_latch,
                            "transientDependencies": transient_dependencies,
                        },
                        expected_type="OUT driven by the alarm's persistent latch ON",
                        related_ids=[str(rung["rungId"]), alarm_id, latch_id],
                    )
                )

    if any(marker in brief_text for marker in ("5초", "timeout", "시간 초과")):
        move_command_ids = {
            signal_id
            for signal_id in io_ids
            if "move_command" in signal_id.casefold() or "move_cmd" in signal_id.casefold()
        }
        for timer_index, timer in enumerate(design["timers"]):
            if int(timer.get("durationMs") or 0) != 5000:
                continue
            if "수동" in brief_text and (
                timer.get("modeId") is not None or timer.get("stepId") is not None
            ):
                diagnostics.append(
                    _diagnostic(
                        "movement_timeout_scoped_out_of_manual_operation",
                        f"/timers/{timer_index}",
                        (
                            "The confirmed five-second timeout applies to both automatic "
                            "and manual moves. Set both modeId and stepId explicitly to null; "
                            "the move-command enable condition provides the shared scope."
                        ),
                        received={
                            "timerId": timer["timerId"],
                            "modeId": timer.get("modeId"),
                            "stepId": timer.get("stepId"),
                        },
                        expected_type="unscoped timer with null modeId and stepId",
                        related_ids=[str(timer["timerId"])],
                    )
                )
            enable_conditions = {
                (str(item["signalId"]), str(item["state"])) for item in timer["enableAll"]
            }
            if move_command_ids and not any(
                (signal_id, "on") in enable_conditions for signal_id in move_command_ids
            ):
                diagnostics.append(
                    _diagnostic(
                        "movement_timeout_not_enabled_by_command",
                        f"/timers/{timer_index}/enableAll",
                        "The five-second movement timeout must run while the move command is ON, not from a completion-derived status.",
                        expected_type="move command signal in state on",
                        related_ids=sorted(move_command_ids),
                    )
                )

    if any(marker in brief_text for marker in ("횟수", "카운트", "count")):
        increment_rungs = [
            rung for rung in design["ladderRungs"] if rung["action"].get("instruction") == "INCP"
        ]
        if not increment_rungs:
            likely_counter_rungs = [
                str(rung["rungId"])
                for rung in design["ladderRungs"]
                if any(
                    marker in f"{rung['rungId']} {rung.get('description', '')}".casefold()
                    for marker in ("counter", "count", "횟수", "카운트")
                )
            ]
            diagnostics.append(
                _diagnostic(
                    "missing_completion_counter_increment",
                    "/ladderRungs",
                    "The confirmed completion count requires an INCP rung writing a D register and a MOV 0 reset rung for the same register.",
                    # This diagnostic means the required writer does not exist. Textual
                    # hints such as "5-second count" can point at an unrelated timer rung,
                    # so they are evidence only and must not lock repair scope to an
                    # existing item. The repair tool is allowed to create the missing rung.
                    received={"possibleCounterRungIds": likely_counter_rungs},
                )
            )
        elif manual_mode_ids:
            manual_step_ids = {
                str(step["stepId"])
                for sequence in sequences
                if sequence.get("modeId") in manual_mode_ids
                for step in sequence["steps"]
            }
            if manual_step_ids and not any(
                any(
                    ("step", step_id, "on") in typed_contacts(rung["condition"])
                    for step_id in manual_step_ids
                )
                for rung in increment_rungs
            ):
                diagnostics.append(
                    _diagnostic(
                        "completion_counter_missing_manual_path",
                        "/ladderRungs",
                        "The confirmed completion count must also increment for a completed manual move, exactly once per manual command.",
                        expected_type="INCP condition containing a manual movement step",
                        related_ids=[
                            *[str(rung["rungId"]) for rung in increment_rungs],
                            *sorted(manual_step_ids),
                        ],
                    )
                )

    exclusive_modes_required = (
        "자동" in brief_text
        and "수동" in brief_text
        and any(marker in brief_text for marker in ("동시", "상호 배타"))
    )
    if exclusive_modes_required:
        activation_ids = {
            str(mode["modeId"]): str(mode["activation"]["signalId"])
            for mode in modes
            if str(mode["modeId"]) in auto_mode_ids | manual_mode_ids
        }

        for network_index, network in enumerate(design["controlNetworks"]):
            output_id = str(network.get("outputSignalId") or "")
            if output_id not in activation_ids.values():
                continue
            permit_ids = {
                str(permit.get("signalId") or "") for permit in network.get("permitAll") or []
            }
            target_mode_id = next(
                mode_id
                for mode_id, activation_id in activation_ids.items()
                if activation_id == output_id
            )
            button_permits = {
                signal_id
                for signal_id in permit_ids
                if "button" in signal_id.casefold() or "_btn" in signal_id.casefold()
            }
            if network.get("modeId") == target_mode_id or output_id in permit_ids or button_permits:
                diagnostics.append(
                    _diagnostic(
                        "mode_network_contains_branch_specific_permits",
                        f"/controlNetworks/{network_index}",
                        (
                            "A latched mode OUT network may declare only interlocks shared by its button-start "
                            "and self-hold branches. In the patch, set mode_id explicitly to null (omitting it "
                            "does not clear a retained value), remove the target self permit and momentary "
                            "button permit, and keep the other mode OFF plus shared safety permits."
                        ),
                        received={
                            "modeId": network.get("modeId"),
                            "permitSignalIds": sorted(permit_ids),
                        },
                        expected_type="only common exclusion and safety permits",
                        related_ids=[str(network["networkId"])],
                    )
                )

        running_signal_ids = {
            signal_id
            for signal_id in io_ids
            if any(marker in signal_id.casefold() for marker in ("system_running", "run_state"))
        }
        for network_index, network in enumerate(design["controlNetworks"]):
            if str(network.get("outputSignalId") or "") not in running_signal_ids:
                continue
            permit_ids = {
                str(permit.get("signalId") or "")
                for permit in network.get("permitAll") or []
                if permit.get("state") == "on"
            }
            if len(permit_ids.intersection(activation_ids.values())) > 1:
                diagnostics.append(
                    _diagnostic(
                        "system_running_network_requires_all_modes",
                        f"/controlNetworks/{network_index}/permitAll",
                        (
                            "The system-running network cannot require mutually exclusive automatic and manual "
                            "modes together. Keep only permits common to both OUT branches, such as start ON; "
                            "the branch-specific mode contacts belong in the OUT rung."
                        ),
                        received=sorted(permit_ids),
                        expected_type="only permits shared by automatic and manual run branches",
                        related_ids=[str(network["networkId"])],
                    )
                )

        def guaranteed_mode_contacts(value: Mapping[str, Any]) -> set[tuple[str, str]]:
            if value.get("kind") == "contact" and value.get("sourceType") == "signal":
                return {(str(value.get("sourceId")), str(value.get("state")))}
            child_contacts = [
                guaranteed_mode_contacts(term)
                for term in value.get("terms") or []
                if isinstance(term, dict)
            ]
            if value.get("kind") == "all_of":
                contacts: set[tuple[str, str]] = set()
                for child in child_contacts:
                    contacts.update(child)
                return contacts
            if value.get("kind") == "any_of" and child_contacts:
                guaranteed = set(child_contacts[0])
                for child in child_contacts[1:]:
                    guaranteed.intersection_update(child)
                return guaranteed
            return set()

        for rung_index, rung in enumerate(design["ladderRungs"]):
            contacts = guaranteed_mode_contacts(rung["condition"])
            simultaneous_on = sorted(
                signal_id for signal_id in set(activation_ids.values()) if (signal_id, "on") in contacts
            )
            if len(simultaneous_on) > 1:
                diagnostics.append(
                    _diagnostic(
                        "mutually_exclusive_modes_required_together",
                        f"/ladderRungs/{rung_index}/condition",
                        "Automatic and manual modes are mutually exclusive; one all_of condition cannot require both mode signals on.",
                        related_ids=simultaneous_on,
                    )
                )

        for mode_id, activation_id in activation_ids.items():
            other_ids = set(activation_ids.values()) - {activation_id}
            mode_rungs = [
                (index, rung)
                for index, rung in enumerate(design["ladderRungs"])
                if rung["action"].get("instruction") == "OUT"
                and rung["action"].get("targetType") == "signal"
                and rung["action"].get("targetId") == activation_id
            ]
            for rung_index, rung in mode_rungs:
                contacts = guaranteed_mode_contacts(rung["condition"])
                if other_ids and not any((other_id, "off") in contacts for other_id in other_ids):
                    diagnostics.append(
                        _diagnostic(
                            "mode_activation_missing_exclusion",
                            f"/ladderRungs/{rung_index}/condition",
                            (
                                f"Mode {mode_id!r} activation must guarantee every other confirmed mode is off. "
                                "For a momentary-button mode latch, keep the other-mode OFF and safety permits "
                                "as outer all_of terms, then use an inner any_of with one independent button-ON "
                                "start branch and one target-mode-ON hold branch. Do not put the target self "
                                "contact in series with its only start path or outside the shared interlocks."
                            ),
                            related_ids=sorted(other_ids),
                        )
                    )

                running_signal_ids = {
                    signal_id
                    for signal_id in io_ids
                    if any(
                        marker in signal_id.casefold()
                        for marker in ("system_running", "run_state", "running")
                    )
                }
                start_paths = independent_start_paths(rung["condition"], activation_id)
                if (
                    "정지" in brief_text
                    and running_signal_ids
                    and start_paths
                    and any(
                        not any(
                            ("signal", signal_id, "off") in path for signal_id in running_signal_ids
                        )
                        for path in start_paths
                    )
                ):
                    diagnostics.append(
                        _diagnostic(
                            "mode_activation_start_missing_stopped_condition",
                            f"/ladderRungs/{rung_index}/condition",
                            (
                                f"Mode {mode_id!r} may only be newly selected while the system is stopped. "
                                "Put the running-state OFF contact in the independent button start branch, "
                                "not in the latched hold branch."
                            ),
                            received={"startPaths": [sorted(path) for path in start_paths]},
                            expected_type="each independent start path contains running-state signal off",
                            related_ids=[str(rung["rungId"]), *sorted(running_signal_ids)],
                        )
                    )

    return diagnostics


def _bound_scope(
    brief: ProjectBriefV1,
    design: dict[str, Any],
    requirement_sources: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    values = brief.model_dump(mode="json")
    supplemental = brief.supplemental_requirements.strip()
    scope: dict[str, Any] = {}
    for name, authority_fields in _SCOPE_AUTHORITY_FIELDS.items():
        evidence = [str(values.get(field) or "").strip() for field in authority_fields]
        evidence.extend(
            str((requirement_sources or {}).get(field) or "").strip()
            for field in authority_fields
        )
        evidence = [item for item in evidence if item]
        if supplemental:
            evidence.append(supplemental)
        if not evidence:
            evidence = [brief.machine_purpose.strip()]
        items = design[name]
        if items or name in {"io_points", "control_networks"}:
            scope[name] = {
                "status": "confirmed",
                "reason": f"Agent proposal: {name} is confirmed with proposed items.",
                "evidence": list(dict.fromkeys(evidence)),
                "basis": None,
            }
        else:
            scope[name] = {
                "status": "not_applicable",
                "reason": f"Agent proposal: {name} is not applicable.",
                "evidence": list(dict.fromkeys(evidence)),
                "basis": _NA_BASES[name],
            }
    return scope


def _narrative_diagnostics(document: dict[str, Any]) -> list[dict[str, Any]]:
    narrative = document.get("narrative")
    if not isinstance(narrative, dict):
        return [_diagnostic("schema_invalid", "/narrative", "Narrative must be an object.")]
    required_text = ("summary", "objective")
    required_lists = (
        "user_requirements",
        "design_proposals",
        "scope",
        "hardware_and_network",
        "io_and_devices",
        "sequence",
        "validation_plan",
    )
    diagnostics = []
    for name in required_text:
        if not isinstance(narrative.get(name), str) or not narrative[name].strip():
            diagnostics.append(
                _diagnostic(
                    "missing_section", f"/narrative/{name}", f"Narrative {name} is required."
                )
            )
    for name in required_lists:
        value = narrative.get(name)
        if not isinstance(value, list) or not value:
            diagnostics.append(
                _diagnostic(
                    "missing_section",
                    f"/narrative/{name}",
                    f"Narrative {name} requires at least one item.",
                )
            )
    questions = narrative.get("open_questions", [])
    if not isinstance(questions, list):
        diagnostics.append(
            _diagnostic(
                "schema_invalid", "/narrative/open_questions", "open_questions must be a list."
            )
        )
    elif questions:
        diagnostics.append(
            _diagnostic(
                "open_decision",
                "/narrative/open_questions",
                "Blocking questions remain in the draft.",
            )
        )
    return diagnostics


def _design_execution_observations(design: Mapping[str, Any]) -> dict[str, Any]:
    """Static dataflow facts for the agent, not a simulation or a publication gate."""
    internal_ids = {
        point["signal_id"] for point in design.get("io_points", [])
        if isinstance(point, dict) and point.get("kind") == "internal_bit"
        and isinstance(point.get("signal_id"), str)
    }
    writers: set[str] = set()
    readers: dict[str, list[str]] = {}
    prior_resets: dict[str, str] = {}
    reset_order: list[dict[str, str]] = []

    def required_on(node: Any) -> set[str]:
        if not isinstance(node, dict):
            return set()
        if node.get("kind") == "contact":
            return {str(node.get("source_id"))} if node.get("source_type") == "signal" and node.get("state") == "on" else set()
        terms = node.get("terms")
        if not isinstance(terms, list) or not terms:
            return set()
        branches = [required_on(term) for term in terms]
        if node.get("kind") == "all_of":
            return set().union(*branches)
        if node.get("kind") == "any_of":
            return set.intersection(*branches)
        return set()

    def contacts(node: Any) -> set[str]:
        if not isinstance(node, dict):
            return set()
        if node.get("kind") == "contact" and node.get("source_type") == "signal":
            return {str(node.get("source_id"))}
        terms = node.get("terms")
        if not isinstance(terms, list):
            return set()
        return set().union(*(contacts(term) for term in terms
                             if isinstance(term, dict)))

    for rung in design.get("ladder_rungs", []):
        if not isinstance(rung, dict):
            continue
        action = rung.get("action") or {}
        rung_id = str(rung.get("rung_id"))
        if action.get("target_type") == "sequence_reset":
            for signal_id in sorted(required_on(rung.get("condition")) & prior_resets.keys()):
                reset_order.append({
                    "signalId": signal_id,
                    "earlierResetRungId": prior_resets[signal_id],
                    "laterSequenceResetRungId": rung_id,
                })
        if action.get("target_type") == "signal" and action.get("instruction") in {"OUT", "SET", "RST"}:
            target_id = str(action.get("target_id"))
            writers.add(target_id)
            if action.get("instruction") == "RST":
                prior_resets[target_id] = rung_id
            else:
                prior_resets.pop(target_id, None)
        for signal_id in contacts(rung.get("condition")) & internal_ids:
            readers.setdefault(signal_id, []).append(str(rung.get("rung_id")))
    return {
        "kind": "static_dataflow_not_runtime_test",
        "potentialResetOrderDependencies": reset_order,
        "resetOrderInstruction": "If an earlier signal RST executes, a later sequence reset requiring that signal ON cannot execute in the same scan. "
        "Read both conditions and use design.move_ladder_rung or edit the recovery logic where needed. These are potential dependencies, not runtime proof.",
        "unwrittenInternalSignals": [
            {"signalId": signal_id, "readerRungIds": readers[signal_id]}
            for signal_id in sorted(readers.keys() - writers)
        ],
        "instruction": "These declared internal bits are read by the program but have no signal writer. "
        "Inspect their intended meaning and implement their producing logic or replace mistaken references. "
        "Do not invent constant values merely to make the list empty.",
    }


def _prioritized_execution_observations(draft: ProjectDesignDraft) -> dict[str, Any]:
    """Keep advisory scan-order hints behind current validation failures."""

    if draft.diagnostics or draft.status not in {"valid", "published"}:
        return {
            "kind": "advisory_suppressed_until_valid_check",
            "diagnosticCount": len(draft.diagnostics),
            "draftStatus": draft.status,
            "instruction": (
                "Run design.check on the current revision first, repairing any returned diagnostics. "
                "Potential scan-order observations are advisory, not validated defects, and are "
                "withheld until this exact revision has a fresh valid check."
            ),
        }
    return _design_execution_observations(draft.document["native_design"])


def _ladder_edit_impact(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, Any]:
    """Report the saved program diff, separately from comment edits."""
    old = {item["rung_id"]: item for item in before}
    new = {item["rung_id"]: item for item in after}
    fields = {
        rung_id: sorted(key for key in old.get(rung_id, {}).keys() | new.get(rung_id, {}).keys()
                        if old.get(rung_id, {}).get(key) != new.get(rung_id, {}).get(key))
        for rung_id in old.keys() | new.keys()
        if old.get(rung_id) != new.get(rung_id)
    }
    executable_changed = (
        [{k: v for k, v in item.items() if k != "description"} for item in before]
        != [{k: v for k, v in item.items() if k != "description"} for item in after]
    )
    return {
        "executableChanged": executable_changed,
        "changedFieldsByRung": fields,
        "observation": (
            "Executable ladder changed; check the saved behavior."
            if executable_changed else
            "Only comments changed. Contacts, actions and execution order are unchanged. "
            "This does not implement a requested behavior correction."
        ),
    }


_INDEX_ACCEPTANCE_INPUT_ROLES = frozenset({
    "automatic_start", "manual_start", "stop", "reset", "select_automatic",
    "select_manual", "emergency_stop", "door_open", "ready", "move_complete",
})
_INDEX_ACCEPTANCE_OUTPUT_ROLES = frozenset({
    "motion", "automatic_mode", "manual_mode", "running", "moving", "any_alarm",
})
_INDEX_ACCEPTANCE_ALARM_ROLES = frozenset({"emergency", "door", "timeout"})


def _index_acceptance_binding_diagnostics(
    design: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Mirror the production runner's explicit role semantics before publication."""

    profile = design.get("indexAcceptanceBindings")
    if profile is None:
        return []
    if not isinstance(profile, Mapping):
        return [_diagnostic(
            "index_acceptance_bindings_invalid",
            "/indexAcceptanceBindings",
            "Index acceptance bindings must be an object.",
            received=type(profile).__name__,
            expected_type="controlstudio-index-acceptance-bindings/v1 object",
        )]

    def entities(collection: str, identity: str) -> dict[str, Mapping[str, Any]]:
        return {
            str(item[identity]): item
            for item in design.get(collection) or []
            if isinstance(item, Mapping) and isinstance(item.get(identity), str)
        }

    signals = profile.get("signals")
    modes = profile.get("modes")
    alarms = profile.get("alarms")
    timer_id = profile.get("timerId")
    expected_signal_roles = _INDEX_ACCEPTANCE_INPUT_ROLES | _INDEX_ACCEPTANCE_OUTPUT_ROLES
    if not isinstance(signals, Mapping) or set(signals) != expected_signal_roles:
        return [_diagnostic(
            "index_acceptance_signal_roles_invalid",
            "/indexAcceptanceBindings/signals",
            "Acceptance signal roles are incomplete or unknown.",
            received=sorted(str(role) for role in signals) if isinstance(signals, Mapping) else signals,
            expected_type="exact automatic/manual index acceptance signal roles",
        )]
    if not isinstance(modes, Mapping) or set(modes) != {"automatic", "manual"}:
        return [_diagnostic(
            "index_acceptance_mode_roles_invalid",
            "/indexAcceptanceBindings/modes",
            "Acceptance mode roles must name automatic and manual exactly.",
            received=sorted(str(role) for role in modes) if isinstance(modes, Mapping) else modes,
            expected_type="automatic and manual mode roles",
        )]
    if not isinstance(alarms, Mapping) or set(alarms) != _INDEX_ACCEPTANCE_ALARM_ROLES:
        return [_diagnostic(
            "index_acceptance_alarm_roles_invalid",
            "/indexAcceptanceBindings/alarms",
            "Acceptance alarm roles must name emergency, door, and timeout exactly.",
            received=sorted(str(role) for role in alarms) if isinstance(alarms, Mapping) else alarms,
            expected_type="emergency, door, and timeout alarm roles",
        )]

    io = entities("ioPoints", "signalId")
    diagnostics: list[dict[str, Any]] = []
    for role, raw_identity in signals.items():
        identity = str(raw_identity)
        expected_kinds = (
            {"digital_input"}
            if role in _INDEX_ACCEPTANCE_INPUT_ROLES
            else {"digital_output", "internal_bit"}
        )
        point = io.get(identity)
        actual_kind = point.get("kind") if point is not None else None
        if point is None or actual_kind not in expected_kinds:
            diagnostics.append(_diagnostic(
                "index_acceptance_signal_role_incompatible",
                f"/indexAcceptanceBindings/signals/{role}",
                f"Acceptance signal role {role!r} has no compatible reviewed signal.",
                received={"signalId": identity, "kind": actual_kind},
                expected_type=" or ".join(sorted(expected_kinds)),
                related_ids=[identity],
            ))
    reverse: dict[str, set[str]] = {}
    for role, identity in signals.items():
        reverse.setdefault(str(identity), set()).add(str(role))
    for identity, aliased_roles in reverse.items():
        if len(aliased_roles) > 1 and aliased_roles != {
            "automatic_start", "manual_start"
        }:
            diagnostics.append(_diagnostic(
                "index_acceptance_signal_roles_alias",
                "/indexAcceptanceBindings/signals",
                "Acceptance signal roles alias unrelated functions.",
                received={"signalId": identity, "roles": sorted(aliased_roles)},
                expected_type="distinct signals except a shared automatic/manual start button",
                related_ids=[identity],
            ))
    if diagnostics:
        return diagnostics

    reviewed_modes = entities("operatingModes", "modeId")
    mode_ids = {str(identity) for identity in modes.values()}
    if len(mode_ids) != 2 or not mode_ids <= reviewed_modes.keys():
        return [_diagnostic(
            "index_acceptance_modes_incompatible",
            "/indexAcceptanceBindings/modes",
            "Acceptance requires distinct reviewed automatic and manual modes.",
            received=dict(modes),
            expected_type="two distinct reviewed mode IDs",
            related_ids=sorted(mode_ids),
        )]
    sequences = entities("sequences", "sequenceId")
    sequence_modes = [item.get("modeId") for item in sequences.values()]
    if len(sequence_modes) != 2 or set(sequence_modes) != mode_ids:
        return [_diagnostic(
            "index_acceptance_sequences_incompatible",
            "/indexAcceptanceBindings/modes",
            "Acceptance requires one sequence for each selected mode.",
            received=sequence_modes,
            expected_type="one sequence per selected automatic/manual mode",
            related_ids=sorted(mode_ids),
        )]
    reviewed_alarms = entities("alarms", "alarmId")
    alarm_ids = {str(identity) for identity in alarms.values()}
    if len(alarm_ids) != 3 or alarm_ids != reviewed_alarms.keys():
        return [_diagnostic(
            "index_acceptance_alarms_incompatible",
            "/indexAcceptanceBindings/alarms",
            "Acceptance must cover all three reviewed fault alarms exactly once.",
            received=dict(alarms),
            expected_type="all three distinct reviewed alarm IDs",
            related_ids=sorted(alarm_ids),
        )]
    latches: dict[str, str] = {}
    for role, identity in alarms.items():
        latch = reviewed_alarms[str(identity)].get("latchSignalId")
        if not isinstance(latch, str) or latch not in io or io[latch].get("kind") != "internal_bit":
            return [_diagnostic(
                "index_acceptance_alarm_latch_incompatible",
                f"/indexAcceptanceBindings/alarms/{role}",
                "Acceptance alarm has no reviewed internal latch.",
                received={"alarmId": identity, "latchSignalId": latch},
                expected_type="alarm with an internal_bit latch signal",
                related_ids=[str(identity), *( [latch] if isinstance(latch, str) else [])],
            )]
        latches[str(role)] = latch
    if len(set(latches.values())) != 3:
        return [_diagnostic(
            "index_acceptance_alarm_latches_alias",
            "/indexAcceptanceBindings/alarms",
            "Acceptance fault alarms must have distinct latch signals.",
            received=latches,
            expected_type="three distinct internal alarm latch signals",
            related_ids=sorted(set(latches.values())),
        )]
    timers = entities("timers", "timerId")
    if not isinstance(timer_id, str) or set(timers) != {timer_id}:
        return [_diagnostic(
            "index_acceptance_timer_incompatible",
            "/indexAcceptanceBindings/timerId",
            "Acceptance must bind the only reviewed timeout timer.",
            received=timer_id,
            expected_type="the sole reviewed timer ID",
            related_ids=[timer_id] if isinstance(timer_id, str) else [],
        )]
    timer = timers[timer_id]
    if timer.get("durationMs") != 5000 or timer.get("instruction") != "TON":
        return [_diagnostic(
            "index_acceptance_timer_semantics_incompatible",
            "/indexAcceptanceBindings/timerId",
            "Index acceptance requires the reviewed five-second TON.",
            received={
                "timerId": timer_id,
                "durationMs": timer.get("durationMs"),
                "instruction": timer.get("instruction"),
            },
            expected_type="TON timer with durationMs 5000",
            related_ids=[timer_id],
        )]
    return []


def _index_acceptance_behavior_diagnostics(
    design: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Check behavior required by the explicit multi-mode index acceptance profile."""

    profile = design.get("indexAcceptanceBindings")
    if not isinstance(profile, Mapping):
        return []
    signals = profile.get("signals")
    modes = profile.get("modes")
    alarms = profile.get("alarms")
    if not all(isinstance(value, Mapping) for value in (signals, modes, alarms)):
        return []
    assert isinstance(signals, Mapping)
    assert isinstance(modes, Mapping)
    assert isinstance(alarms, Mapping)

    def paths(node: Any) -> list[set[tuple[str, str, str]]]:
        if not isinstance(node, Mapping):
            return []
        if node.get("kind") == "contact":
            return [{(
                str(node.get("sourceType")),
                str(node.get("sourceId")),
                str(node.get("state")),
            )}]
        terms = [term for term in node.get("terms") or [] if isinstance(term, Mapping)]
        if node.get("kind") == "any_of":
            return [path for term in terms for path in paths(term)]
        if node.get("kind") == "all_of":
            combined: list[set[tuple[str, str, str]]] = [set()]
            for term in terms:
                term_paths = paths(term)
                if not term_paths:
                    return []
                combined = [left | right for left in combined for right in term_paths]
            return combined
        return []

    rungs = [item for item in design.get("ladderRungs") or [] if isinstance(item, Mapping)]

    def signal_writers(signal_id: str, instruction: str | None = None) -> list[tuple[int, Mapping[str, Any]]]:
        return [
            (index, rung)
            for index, rung in enumerate(rungs)
            if isinstance(rung.get("action"), Mapping)
            and rung["action"].get("targetType") == "signal"
            and str(rung["action"].get("targetId")) == signal_id
            and (instruction is None or rung["action"].get("instruction") == instruction)
        ]

    diagnostics: list[dict[str, Any]] = []
    any_alarm = str(signals.get("any_alarm"))
    reviewed_alarms = {
        str(item.get("alarmId")): item
        for item in design.get("alarms") or []
        if isinstance(item, Mapping)
    }
    selected_alarm_ids = {str(value) for value in alarms.values()}
    selected_latches = {
        str(reviewed_alarms[alarm_id].get("latchSignalId"))
        for alarm_id in selected_alarm_ids
        if alarm_id in reviewed_alarms
    }
    blocked_recovery: dict[str, Any] | None = None

    def alarm_off_dependent_states(condition_paths: Sequence[set[tuple[str, str, str]]]) -> list[str]:
        blocked: set[str] = set()
        for condition_path in condition_paths:
            for source_type, state_id, state in condition_path:
                if source_type != "signal" or state != "on":
                    continue
                writers = signal_writers(state_id, "OUT")
                writer_paths = [
                    writer_path
                    for _index, writer in writers
                    for writer_path in paths(writer.get("condition"))
                ]
                if writer_paths and all(
                    ("signal", any_alarm, "off") in writer_path
                    for writer_path in writer_paths
                ):
                    blocked.add(state_id)
        return sorted(blocked)

    for recovery_index, recovery in enumerate(design.get("recoveryRules") or []):
        if not isinstance(recovery, Mapping) or not (
            selected_alarm_ids & {str(value) for value in recovery.get("resetAlarmIds") or []}
        ):
            continue
        declared_path = {
            ("signal", str(condition.get("signalId")), str(condition.get("state")))
            for condition in recovery.get("whenAll") or []
            if isinstance(condition, Mapping)
        }
        blocked_states = alarm_off_dependent_states([declared_path])
        if blocked_states:
            blocked_recovery = {
                "path": f"/recoveryRules/{recovery_index}/whenAll",
                "blockedStateSignalIds": blocked_states,
                "relatedIds": [str(recovery.get("recoveryId"))],
            }
            break
    if blocked_recovery is None:
        for latch in sorted(selected_latches):
            for rung_index, rung in signal_writers(latch, "RST"):
                blocked_states = alarm_off_dependent_states(paths(rung.get("condition")))
                if blocked_states:
                    blocked_recovery = {
                        "path": f"/ladderRungs/{rung_index}/condition",
                        "blockedStateSignalIds": blocked_states,
                        "relatedIds": [str(rung.get("rungId")), latch],
                    }
                    break
            if blocked_recovery is not None:
                break
    if blocked_recovery is not None:
        blocked_states = blocked_recovery["blockedStateSignalIds"]
        diagnostics.append(_diagnostic(
            "index_acceptance_recovery_blocked_by_alarm_state",
            blocked_recovery["path"],
            "Alarm recovery requires a derived state that can only be ON while any_alarm is OFF. "
            "Cause-clear and reset must remain satisfiable while the selected alarm latch is still ON.",
            received={"blockedStateSignalIds": blocked_states},
            expected_type="recovery conditions satisfiable before the alarm latch is reset",
            related_ids=[*blocked_recovery["relatedIds"], any_alarm, *blocked_states],
        ))

    stop = str(signals.get("stop"))
    governed_roles = ("motion", "running", "moving")
    missing_stop: list[dict[str, Any]] = []
    for role in governed_roles:
        target = str(signals.get(role))
        writers = signal_writers(target, "OUT")
        if not writers:
            missing_stop.append({"role": role, "signalId": target, "rungIds": []})
            continue
        bad_rungs = [
            str(rung.get("rungId"))
            for _index, rung in writers
            if any(("signal", stop, "off") not in path for path in paths(rung.get("condition")))
        ]
        if bad_rungs:
            missing_stop.append({"role": role, "signalId": target, "rungIds": bad_rungs})
    stop_alarm_dependencies = [
        str(alarm.get("alarmId"))
        for alarm in reviewed_alarms.values()
        if any(
            isinstance(item, Mapping) and str(item.get("signalId")) == stop
            for item in alarm.get("triggerAll") or []
        )
    ]
    stop_alarm_dependencies.extend(
        str(rung.get("rungId"))
        for latch in selected_latches
        for _index, rung in signal_writers(latch, "SET")
        if any(("signal", stop, state) in path
               for path in paths(rung.get("condition")) for state in ("on", "off"))
    )
    if missing_stop or stop_alarm_dependencies:
        diagnostics.append(_diagnostic(
            "index_acceptance_normal_stop_not_enforced",
            "/indexAcceptanceBindings/signals/stop",
            "The bound normal-stop input must deenergize motion, running and moving on every OUT path "
            "without becoming a fault-alarm cause.",
            received={
                "unguardedOutputs": missing_stop,
                "alarmDependencies": sorted(set(stop_alarm_dependencies)),
            },
            expected_type="normal stop OFF permits all run outputs; stop ON creates no alarm",
            related_ids=[stop, *[item["signalId"] for item in missing_stop]],
        ))

    # A normal-stop contact only deenergizes motion while the button is held.
    # Once it is released, a retained automatic movement step can otherwise
    # re-energize the actuator even though the automatic run latch is still
    # OFF. Require that latch on every automatic-step motion path. Manual paths
    # remain independent because this role may intentionally name only the
    # automatic retained run bit.
    running = str(signals.get("running"))
    motion = str(signals.get("motion"))
    automatic_mode_id = str(modes.get("automatic"))
    automatic_step_ids = {
        str(step.get("stepId"))
        for sequence in design.get("sequences") or []
        if isinstance(sequence, Mapping)
        and str(sequence.get("modeId")) == automatic_mode_id
        for step in sequence.get("steps") or []
        if isinstance(step, Mapping)
    }
    motion_writers = signal_writers(motion, "OUT")
    motion_without_running = [
        {
            "rungId": str(rung.get("rungId")),
            "rungIndex": rung_index,
        }
        for rung_index, rung in motion_writers
        if any(
            any(("step", step_id, "on") in condition_path for step_id in automatic_step_ids)
            and ("signal", running, "on") not in condition_path
            for condition_path in paths(rung.get("condition"))
        )
    ]
    if motion_without_running:
        diagnostics.append(_diagnostic(
            "index_acceptance_motion_not_gated_by_running",
            (
                f"/ladderRungs/{motion_without_running[0]['rungIndex']}/condition"
                if motion_without_running else "/indexAcceptanceBindings/signals/motion"
            ),
            "Every automatic-step path to the physical motion output must require the retained "
            "automatic running state. Otherwise releasing normal stop can re-energize a retained "
            "automatic movement step while the machine remains stopped.",
            received={"unguardedWriters": motion_without_running},
            expected_type="every automatic-step motion path contains running signal ON",
            related_ids=[
                motion, running,
                *[item["rungId"] for item in motion_without_running],
            ],
        ))

    # A retained manual step is just as capable of re-energizing motion after
    # a momentary normal stop as an automatic step.  Manual motion needs its
    # own retained run permit, cleared by normal stop/reset, while operator
    # reset must reset both selected sequences even when no alarm is latched.
    # Otherwise the stopped-counter-reset scenario releases stop into the old
    # manual movement step, which eventually creates a new timeout alarm.
    manual_mode_id = str(modes.get("manual"))
    manual_mode_signal = str(signals.get("manual_mode"))
    manual_start = str(signals.get("manual_start"))
    reset_signal = str(signals.get("reset"))
    manual_running = next(
        (
            str(point.get("signalId"))
            for point in design.get("ioPoints") or []
            if isinstance(point, Mapping)
            and str(point.get("signalId")) == "manual_running"
        ),
        "",
    )
    selected_sequences = [
        sequence
        for sequence in design.get("sequences") or []
        if isinstance(sequence, Mapping)
        and str(sequence.get("modeId")) in {automatic_mode_id, manual_mode_id}
    ]
    manual_step_ids = {
        str(step.get("stepId"))
        for sequence in selected_sequences
        if str(sequence.get("modeId")) == manual_mode_id
        for step in sequence.get("steps") or []
        if isinstance(step, Mapping)
    }
    manual_terminal_steps = {
        str(steps[-1].get("stepId"))
        for sequence in selected_sequences
        if str(sequence.get("modeId")) == manual_mode_id
        and (steps := [
            step for step in sequence.get("steps") or []
            if isinstance(step, Mapping)
        ])
    }
    manual_motion_without_running = [
        {"rungId": str(rung.get("rungId")), "rungIndex": rung_index}
        for rung_index, rung in motion_writers
        if any(
            any(("step", step_id, "on") in condition_path for step_id in manual_step_ids)
            and (not manual_running or ("signal", manual_running, "on") not in condition_path)
            for condition_path in paths(rung.get("condition"))
        )
    ]
    manual_running_writers = signal_writers(manual_running, "OUT") if manual_running else []
    healthy_manual_running_writer = any(
        writer_paths
        and all(
            ("signal", stop, "off") in writer_path
            and ("signal", reset_signal, "off") in writer_path
            and ("signal", manual_mode_signal, "on") in writer_path
            and any(
                ("step", terminal_step, "off") in writer_path
                for terminal_step in manual_terminal_steps
            )
            and (
                ("signal", manual_start, "on") in writer_path
                or ("signal", manual_running, "on") in writer_path
            )
            for writer_path in writer_paths
        )
        for _index, writer in manual_running_writers
        if (writer_paths := paths(writer.get("condition")))
    )
    reset_missing_sequences: list[str] = []
    for sequence in selected_sequences:
        sequence_id = str(sequence.get("sequenceId"))
        reset_writers = [
            rung
            for rung in rungs
            if isinstance(rung.get("action"), Mapping)
            and rung["action"].get("instruction") == "SET"
            and rung["action"].get("targetType") == "sequence_reset"
            and str(rung["action"].get("targetId")) == sequence_id
        ]
        if not any(
            condition_paths
            and all(
                ("signal", reset_signal, "on") in condition_path
                for condition_path in condition_paths
            )
            for rung in reset_writers
            if (condition_paths := paths(rung.get("condition")))
        ):
            reset_missing_sequences.append(sequence_id)
    if (
        manual_step_ids
        and (
            manual_motion_without_running
            or not healthy_manual_running_writer
            or reset_missing_sequences
        )
    ):
        candidate_run_rungs = [
            str(rung.get("rungId"))
            for point_id in ("manual_move_pressed", manual_running)
            for _index, rung in signal_writers(point_id, "OUT")
        ]
        diagnostics.append(_diagnostic(
            "index_acceptance_manual_stop_reset_can_resume_motion",
            "/indexAcceptanceBindings/signals/manualStart",
            "Manual motion must use a retained run permit that normal stop and reset clear, "
            "and reset must reset both selected sequences without requiring an alarm. "
            "Otherwise a retained manual step can resume after stop and create a timeout.",
            received={
                "unguardedMotionWriters": manual_motion_without_running,
                "manualRunningSignalId": manual_running or None,
                "manualRunningWriterIds": [
                    str(rung.get("rungId")) for _index, rung in manual_running_writers
                ],
                "candidateRunRungIds": list(dict.fromkeys(candidate_run_rungs)),
                "resetMissingSequenceIds": reset_missing_sequences,
            },
            expected_type=(
                "manual-step motion gated by a stop/reset-cleared manual run permit, "
                "with unconditional operator-reset sequence reset rungs"
            ),
            related_ids=[
                motion, stop, reset_signal, manual_start,
                *(manual_running and [manual_running] or []),
                *manual_step_ids, *reset_missing_sequences,
            ],
        ))

    # The timeout-done mirror is written later in the scan than the timeout
    # latch and its reset rungs in normal generated programs.  Requiring that
    # mirror to be OFF on the reset path makes a one-scan reset pulse race the
    # previous scan's retained value: the counter can reset while the timeout
    # latch (and therefore any_alarm) remains ON.  Timeout is not an external
    # cause that an operator must clear like E-stop or a door input; the reset
    # action itself stops/resets the affected sequences.  Reject this stale
    # scan-order dependency in both the reviewed recovery contract and its
    # executable RST/sequence-reset rungs.
    timeout_alarm_id = str(alarms.get("timeout"))
    timeout_alarm = reviewed_alarms.get(timeout_alarm_id)
    timeout_latch = (
        str(timeout_alarm.get("latchSignalId"))
        if isinstance(timeout_alarm, Mapping) else ""
    )
    reset_signal = str(signals.get("reset"))
    timer_id = str(profile.get("timerId"))
    timeout_done_signals = {
        str(rung["action"].get("targetId"))
        for _index, rung in enumerate(rungs)
        if isinstance(rung.get("action"), Mapping)
        and rung["action"].get("instruction") == "OUT"
        and rung["action"].get("targetType") == "signal"
        and any(
            ("timer", timer_id, "on") in condition_path
            for condition_path in paths(rung.get("condition"))
        )
    }
    unsafe_recoveries: list[dict[str, Any]] = []
    reset_sequence_ids: set[str] = set()
    for recovery_index, recovery in enumerate(design.get("recoveryRules") or []):
        if not isinstance(recovery, Mapping) or timeout_alarm_id not in {
            str(value) for value in recovery.get("resetAlarmIds") or []
        }:
            continue
        reset_sequence_ids.update(str(value) for value in recovery.get("resetSequenceIds") or [])
        stale_gates = sorted({
            str(item.get("signalId"))
            for item in recovery.get("whenAll") or []
            if isinstance(item, Mapping)
            and item.get("state") == "off"
            and str(item.get("signalId")) in timeout_done_signals
        })
        if stale_gates:
            unsafe_recoveries.append({
                "recoveryId": str(recovery.get("recoveryId")),
                "recoveryIndex": recovery_index,
                "timeoutDoneSignalIds": stale_gates,
            })
    unsafe_timeout_rungs: list[dict[str, Any]] = []
    for rung_index, rung in enumerate(rungs):
        action = rung.get("action")
        if not isinstance(action, Mapping):
            continue
        is_timeout_reset = (
            action.get("instruction") == "RST"
            and action.get("targetType") == "signal"
            and str(action.get("targetId")) == timeout_latch
        ) or (
            action.get("instruction") == "SET"
            and action.get("targetType") == "sequence_reset"
            and str(action.get("targetId")) in reset_sequence_ids
        )
        if not is_timeout_reset:
            continue
        stale_gates = sorted({
            source_id
            for condition_path in paths(rung.get("condition"))
            for source_type, source_id, state in condition_path
            if source_type == "signal"
            and source_id in timeout_done_signals
            and state == "off"
        })
        if stale_gates:
            unsafe_timeout_rungs.append({
                "rungId": str(rung.get("rungId")),
                "rungIndex": rung_index,
                "timeoutDoneSignalIds": stale_gates,
            })
    if unsafe_recoveries or unsafe_timeout_rungs:
        first_path = (
            f"/recoveryRules/{unsafe_recoveries[0]['recoveryIndex']}/whenAll"
            if unsafe_recoveries
            else f"/ladderRungs/{unsafe_timeout_rungs[0]['rungIndex']}/condition"
        )
        diagnostics.append(_diagnostic(
            "index_acceptance_timeout_reset_blocked_by_done_mirror",
            first_path,
            "Timeout recovery must not require the later-written timeout-done mirror to be OFF. "
            "A one-scan reset must clear the timeout latch and both affected sequences even when "
            "the mirror still contains the previous scan's ON value.",
            received={
                "unsafeRecoveries": unsafe_recoveries,
                "unsafeRungs": unsafe_timeout_rungs,
                "timeoutDoneSignalIds": sorted(timeout_done_signals),
            },
            expected_type="reset ON clears timeout latch and reset sequences without timeout-done OFF",
            related_ids=[
                timeout_alarm_id, timeout_latch, reset_signal,
                *sorted(timeout_done_signals),
                *[item["recoveryId"] for item in unsafe_recoveries],
                *[item["rungId"] for item in unsafe_timeout_rungs],
            ],
        ))

    mode_items = {
        str(item.get("modeId")): item
        for item in design.get("operatingModes") or []
        if isinstance(item, Mapping)
    }
    mode_issues: list[dict[str, Any]] = []
    mode_specs = (
        ("automatic", "automatic_mode", "select_automatic", "manual_mode"),
        ("manual", "manual_mode", "select_manual", "automatic_mode"),
    )
    for mode_role, target_role, selector_role, other_role in mode_specs:
        mode_id = str(modes.get(mode_role))
        target = str(signals.get(target_role))
        selector = str(signals.get(selector_role))
        other = str(signals.get(other_role))
        declared = mode_items.get(mode_id, {})
        if str(modes.get("manual" if mode_role == "automatic" else "automatic")) in {
            str(value) for value in declared.get("allowedTransitions") or []
        }:
            mode_issues.append({"modeRole": mode_role, "issue": "direct_transition_declared"})
        writers = signal_writers(target, "OUT")
        if len(writers) != 1:
            mode_issues.append({"modeRole": mode_role, "issue": "single_out_writer_required"})
            continue
        rung_index, rung = writers[0]
        rung_paths = paths(rung.get("condition"))
        opposite_selector = str(signals.get(
            "select_manual" if mode_role == "automatic" else "select_automatic"
        ))
        start_paths = [
            path for path in rung_paths
            if ("signal", target, "on") not in path
            and ("signal", selector, "on") in path
        ]
        hold_paths = [path for path in rung_paths if ("signal", target, "on") in path]
        if (
            not start_paths
            or any(("signal", running, "off") not in path for path in start_paths)
            or not hold_paths
            or any(
                any(("signal", selector, state) in path for state in ("on", "off"))
                or (
                    ("signal", running, "on") not in path
                    and ("signal", opposite_selector, "off") not in path
                )
                for path in hold_paths
            )
            or any(("signal", other, "off") not in path for path in rung_paths)
        ):
            mode_issues.append({
                "modeRole": mode_role,
                "issue": "stopped_start_or_selector_independent_hold_missing",
                "rungId": str(rung.get("rungId")),
                "rungIndex": rung_index,
            })
    if mode_issues:
        first = mode_issues[0]
        diagnostics.append(_diagnostic(
            "index_acceptance_mode_selection_not_retained_until_stopped",
            (
                f"/ladderRungs/{first['rungIndex']}/condition"
                if "rungIndex" in first else "/operatingModes"
            ),
            "Automatic and manual mode selection must start only while running is OFF, remain latched "
            "during operation, yield to the opposite selector while stopped, and guarantee the other "
            "mode is OFF.",
            received={"issues": mode_issues},
            expected_type=(
                "stopped-only start branch plus exclusive hold branch retained while running or until "
                "an opposite stopped selection"
            ),
            related_ids=[
                str(signals.get("automatic_mode")),
                str(signals.get("manual_mode")),
                *[
                    str(issue["rungId"])
                    for issue in mode_issues
                    if issue.get("rungId")
                ],
            ],
        ))

    # A derived "stopped" bit is useful for mode selection, but it must never
    # be required by a transition that is already inside automatic/manual
    # operation.  In that case the stopped writer (normally both modes OFF)
    # and the active-mode/step contact make the transition unsatisfiable.  This
    # escaped schema/reference validation because every individual reference
    # was valid; check the cross-rung Boolean contract explicitly.
    io = {
        str(item.get("signalId")): item
        for item in design.get("ioPoints") or []
        if isinstance(item, Mapping)
    }
    automatic_mode_signal = str(signals.get("automatic_mode"))
    manual_mode_signal = str(signals.get("manual_mode"))
    stopped_markers = (
        "mode_stopped", "mode stopped", "stopped mode", "정지 모드", "정지모드",
        "정지 상태", "정지상태",
    )
    stopped_signal_ids: set[str] = set()
    for signal_id, item in io.items():
        if item.get("kind") != "internal_bit":
            continue
        identity_text = " ".join(
            str(item.get(field) or "")
            for field in ("signalId", "symbol", "name")
        ).casefold()
        description = str(item.get("description") or "").strip().casefold()
        if (
            any(marker in identity_text for marker in stopped_markers)
            or description.startswith((
                "stopped mode state", "mode stopped state", "정지 모드 상태",
                "정지모드 상태", "정지 상태",
            ))
        ):
            stopped_signal_ids.add(signal_id)
            continue
        writer_paths = [
            writer_path
            for _index, writer in signal_writers(signal_id, "OUT")
            for writer_path in paths(writer.get("condition"))
        ]
        if writer_paths and all(
            ("signal", automatic_mode_signal, "off") in writer_path
            and ("signal", manual_mode_signal, "off") in writer_path
            for writer_path in writer_paths
        ):
            stopped_signal_ids.add(signal_id)

    sequence_roles = {
        str(modes.get("automatic")): automatic_mode_signal,
        str(modes.get("manual")): manual_mode_signal,
    }
    blocked_sequence_rungs: list[dict[str, Any]] = []

    def record_blocked_sequence_rung(
        *, rung: Mapping[str, Any], rung_index: int, sequence_id: str,
        stopped_ids: set[str], contract_path: str | None = None,
        mode_id: str = "", active_mode_signal_id: str = "",
    ) -> None:
        rung_id = str(rung.get("rungId"))
        existing = next(
            (
                item for item in blocked_sequence_rungs
                if item["rungId"] == rung_id and item["sequenceId"] == sequence_id
            ),
            None,
        )
        if existing is None:
            existing = {
                "rungId": rung_id, "rungIndex": rung_index,
                "sequenceId": sequence_id, "stoppedSignalIds": [],
                "contractPaths": [],
            }
            blocked_sequence_rungs.append(existing)
        if mode_id:
            existing["modeId"] = mode_id
        if active_mode_signal_id:
            existing["activeModeSignalId"] = active_mode_signal_id
        existing["stoppedSignalIds"] = sorted({
            *existing["stoppedSignalIds"], *stopped_ids,
        })
        if contract_path and contract_path not in existing["contractPaths"]:
            existing["contractPaths"].append(contract_path)

    for sequence_index, sequence in enumerate(design.get("sequences") or []):
        if not isinstance(sequence, Mapping):
            continue
        mode_signal = sequence_roles.get(str(sequence.get("modeId")))
        if not mode_signal:
            continue
        steps = [
            step for step in sequence.get("steps") or [] if isinstance(step, Mapping)
        ]
        step_ids = {
            str(step.get("stepId"))
            for step in steps
        }
        if not step_ids:
            continue
        sequence_id = str(sequence.get("sequenceId"))
        mode_id = str(sequence.get("modeId"))
        active_proofs = {
            ("signal", mode_signal, "on"),
            ("signal", running, "on"),
            *{("step", step_id, "on") for step_id in step_ids},
        }
        mode_item = mode_items.get(mode_id)
        mode_activation = (
            mode_item.get("activation") if isinstance(mode_item, Mapping) else None
        )
        activation_gates = (
            {str(mode_activation.get("signalId"))}
            if isinstance(mode_activation, Mapping)
            and mode_activation.get("state") == "on"
            and str(mode_activation.get("signalId")) in stopped_signal_ids
            else set()
        )
        if activation_gates:
            mode_index = next(
                index for index, item in enumerate(design.get("operatingModes") or [])
                if isinstance(item, Mapping) and str(item.get("modeId")) == mode_id
            )
            for rung_index, rung in enumerate(rungs):
                action = rung.get("action")
                rung_paths = paths(rung.get("condition"))
                is_sequence_action = (
                    isinstance(action, Mapping)
                    and action.get("instruction") == "SET"
                    and (
                        (
                            action.get("targetType") == "step"
                            and str(action.get("targetId")) in step_ids
                        )
                        or (
                            action.get("targetType") == "sequence_reset"
                            and str(action.get("targetId")) == sequence_id
                        )
                    )
                )
                proves_active_or_start = any(
                    condition_path & active_proofs
                    or ("sequence_reset", sequence_id, "on") in condition_path
                    for condition_path in rung_paths
                )
                if is_sequence_action and proves_active_or_start:
                    record_blocked_sequence_rung(
                        rung=rung, rung_index=rung_index, sequence_id=sequence_id,
                        stopped_ids=activation_gates,
                        contract_path=f"/operatingModes/{mode_index}/activation",
                        mode_id=mode_id, active_mode_signal_id=mode_signal,
                    )
        declared_start_gates = {
            str(item.get("signalId"))
            for item in sequence.get("startAll") or []
            if isinstance(item, Mapping)
            and item.get("state") == "on"
            and str(item.get("signalId")) in stopped_signal_ids
        }
        first_step_id = str(steps[0].get("stepId"))
        if declared_start_gates:
            for rung_index, rung in enumerate(rungs):
                action = rung.get("action")
                if (
                    isinstance(action, Mapping)
                    and action.get("instruction") == "SET"
                    and action.get("targetType") == "step"
                    and str(action.get("targetId")) == first_step_id
                    and any(
                        ("sequence_reset", sequence_id, "on") in condition_path
                        for condition_path in paths(rung.get("condition"))
                    )
                ):
                    record_blocked_sequence_rung(
                        rung=rung, rung_index=rung_index, sequence_id=sequence_id,
                        stopped_ids=declared_start_gates,
                        contract_path=f"/sequences/{sequence_index}/startAll",
                        mode_id=mode_id, active_mode_signal_id=mode_signal,
                    )
        for step_index, step in enumerate(steps):
            declared_complete_gates = {
                str(item.get("signalId"))
                for item in step.get("completeAll") or []
                if isinstance(item, Mapping)
                and item.get("state") == "on"
                and str(item.get("signalId")) in stopped_signal_ids
            }
            if not declared_complete_gates:
                continue
            source_step_id = str(step.get("stepId"))
            target_type = "step" if step_index + 1 < len(steps) else "sequence_reset"
            target_id = (
                str(steps[step_index + 1].get("stepId"))
                if target_type == "step" else sequence_id
            )
            for rung_index, rung in enumerate(rungs):
                action = rung.get("action")
                if (
                    isinstance(action, Mapping)
                    and action.get("instruction") == "SET"
                    and action.get("targetType") == target_type
                    and str(action.get("targetId")) == target_id
                    and any(
                        ("step", source_step_id, "on") in condition_path
                        for condition_path in paths(rung.get("condition"))
                    )
                ):
                    record_blocked_sequence_rung(
                        rung=rung, rung_index=rung_index, sequence_id=sequence_id,
                        stopped_ids=declared_complete_gates,
                        contract_path=(
                            f"/sequences/{sequence_index}/steps/{step_index}/completeAll"
                        ),
                        mode_id=mode_id, active_mode_signal_id=mode_signal,
                    )
        for rung_index, rung in enumerate(rungs):
            action = rung.get("action")
            if not (
                isinstance(action, Mapping)
                and action.get("instruction") == "SET"
                and (
                    (
                        action.get("targetType") == "step"
                        and str(action.get("targetId")) in step_ids
                    )
                    or (
                        action.get("targetType") == "sequence_reset"
                        and str(action.get("targetId")) == sequence_id
                    )
                )
            ):
                continue
            rung_paths = paths(rung.get("condition"))
            path_gates = [
                {
                    source_id for source_type, source_id, state in condition_path
                    if source_type == "signal"
                    and state == "on"
                    and source_id in stopped_signal_ids
                }
                for condition_path in rung_paths
            ]
            if (
                rung_paths
                and all(path_gate for path_gate in path_gates)
                and all(condition_path & active_proofs for condition_path in rung_paths)
            ):
                record_blocked_sequence_rung(
                    rung=rung, rung_index=rung_index, sequence_id=sequence_id,
                    stopped_ids=set().union(*path_gates),
                    mode_id=mode_id, active_mode_signal_id=mode_signal,
                )
    if blocked_sequence_rungs:
        first = blocked_sequence_rungs[0]
        diagnostics.append(_diagnostic(
            "index_acceptance_active_sequence_blocked_by_stopped_signal",
            f"/ladderRungs/{first['rungIndex']}/condition",
            "An active automatic/manual sequence contract requires a stopped-state signal "
            "whose own contract requires operation to be inactive. Correct the bound mode or "
            "sequence declaration and remove only its corresponding stopped gate from the "
            "diagnosed active transition rungs.",
            received={"blockedRungs": blocked_sequence_rungs},
            expected_type="active sequence transitions satisfiable while their mode or prior step is ON",
            related_ids=[
                *[item["rungId"] for item in blocked_sequence_rungs],
                *sorted({
                    signal_id
                    for item in blocked_sequence_rungs
                    for signal_id in item["stoppedSignalIds"]
                }),
            ],
        ))

    automatic_start = str(signals.get("automatic_start"))
    run_writers = signal_writers(running, "OUT")
    rearm_ok = False
    for run_index, run_rung in run_writers:
        for path in paths(run_rung.get("condition")):
            if ("signal", automatic_start, "on") not in path or (
                "signal", running, "on"
            ) in path:
                continue
            for _source_type, candidate, state in path:
                if (
                    state != "on"
                    or candidate in {str(value) for value in signals.values()}
                    or io.get(candidate, {}).get("kind") != "internal_bit"
                ):
                    continue
                for writer_index, writer in signal_writers(candidate, "OUT"):
                    writer_paths = paths(writer.get("condition"))
                    if writer_index > run_index and writer_paths and all(
                        ("signal", automatic_start, "off") in writer_path
                        for writer_path in writer_paths
                    ):
                        rearm_ok = True
                release_sets = [
                    (writer_index, writer)
                    for writer_index, writer in signal_writers(candidate, "SET")
                    if any(
                        ("signal", automatic_start, "off") in writer_path
                        for writer_path in paths(writer.get("condition"))
                    )
                ]
                consume_resets = [
                    (writer_index, writer)
                    for writer_index, writer in signal_writers(candidate, "RST")
                    if writer_index > run_index
                    and any(
                        ("signal", automatic_start, "on") in writer_path
                        for writer_path in paths(writer.get("condition"))
                    )
                ]
                if release_sets and consume_resets:
                    rearm_ok = True
    if not rearm_ok:
        diagnostics.append(_diagnostic(
            "index_acceptance_automatic_start_rearm_missing",
            (
                f"/ladderRungs/{run_writers[0][0]}/condition"
                if run_writers else "/ladderRungs"
            ),
            "Automatic start needs a release-armed edge path. Holding start through stop, reset or "
            "fault recovery must not energize running again until start is released and pressed anew.",
            received={
                "runningSignalId": running,
                "startSignalId": automatic_start,
                "runningRungIds": [str(rung.get("rungId")) for _index, rung in run_writers],
                "requiredRepair": {
                    "declarationKind": "internal_bit",
                    "releaseWriterCondition": {"signalId": automatic_start, "state": "off"},
                    "startPathRequiresArm": True,
                    "armConsumedAfterRunningEvaluation": True,
                },
            },
            expected_type="start path gated by an internal release arm consumed after the start scan",
            related_ids=[
                running,
                automatic_start,
                stop,
                *[str(rung.get("rungId")) for _index, rung in run_writers],
            ],
        ))
    return diagnostics


def _condition_mentions_any_signal(
    condition: Any, signal_ids: set[str]
) -> bool:
    if not isinstance(condition, Mapping):
        return False
    if (
        condition.get("kind") == "contact"
        and condition.get("source_type") == "signal"
        and str(condition.get("source_id") or "") in signal_ids
    ):
        return True
    return any(
        _condition_mentions_any_signal(term, signal_ids)
        for term in condition.get("terms") or []
    )


def _condition_has_exact_contact(
    condition: Any, *, source_type: str, source_id: str, state: str,
) -> bool:
    if not isinstance(condition, Mapping):
        return False
    if (
        condition.get("kind") == "contact"
        and condition.get("source_type") == source_type
        and condition.get("source_id") == source_id
        and condition.get("state") == state
    ):
        return True
    return any(
        _condition_has_exact_contact(
            term, source_type=source_type, source_id=source_id, state=state
        )
        for term in condition.get("terms") or []
    )


def _prune_exact_stale_step_contact(
    condition: Mapping[str, Any], path: Sequence[str], stale_id: str,
) -> dict[str, Any] | None:
    """Remove one validator-addressed undeclared step contact and normalize."""

    if list(path) == ["sourceId"]:
        if not (
            condition.get("kind") == "contact"
            and condition.get("source_type") == "step"
            and condition.get("source_id") == stale_id
        ):
            raise ProjectDesignDraftConflict("stale step diagnostic no longer matches its contact")
        return None
    if len(path) < 3 or path[0] != "terms" or not str(path[1]).isdigit():
        raise ProjectDesignDraftConflict("stale step diagnostic path is not a condition branch")
    terms = [
        _json_clone(term) for term in condition.get("terms") or []
        if isinstance(term, Mapping)
    ]
    index = int(path[1])
    if index >= len(terms):
        raise ProjectDesignDraftConflict("stale step diagnostic branch is no longer present")
    repaired = _prune_exact_stale_step_contact(terms[index], path[2:], stale_id)
    if repaired is None:
        terms.pop(index)
    else:
        terms[index] = repaired
    if not terms:
        raise ProjectDesignDraftConflict("stale step pruning would empty a rung condition")
    if len(terms) == 1:
        return terms[0]
    return {"kind": str(condition.get("kind")), "terms": terms}


def _stale_step_reference_repair_changes(
    design: Mapping[str, Any], diagnostics: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Repair only exact checked ladder contacts for undeclared step IDs."""

    declared_steps = {
        str(step.get("step_id") or "")
        for sequence in design.get("sequences") or [] if isinstance(sequence, Mapping)
        for step in sequence.get("steps") or [] if isinstance(step, Mapping)
    }
    rungs = [item for item in design.get("ladder_rungs") or [] if isinstance(item, Mapping)]
    conditions: dict[int, dict[str, Any]] = {}
    remove_indices: set[int] = set()
    for diagnostic in diagnostics:
        if (
            diagnostic.get("code") != "unknown_reference"
            or str(diagnostic.get("expectedType") or "") != "step"
        ):
            continue
        stale_id = str(diagnostic.get("received") or "")
        if not stale_id or stale_id in declared_steps:
            raise ProjectDesignDraftConflict("stale step diagnostic is no longer current")
        match = re.fullmatch(
            r"/ladderRungs/(\d+)/condition/(.+)/sourceId",
            str(diagnostic.get("path") or ""),
        )
        if match is None or int(match.group(1)) >= len(rungs):
            raise ProjectDesignDraftConflict("stale step diagnostic has no retained rung")
        index = int(match.group(1))
        rung = rungs[index]
        action = rung.get("action")
        if (
            isinstance(action, Mapping)
            and action.get("instruction") == "SET"
            and action.get("target_type") == "step"
        ):
            target = str(action.get("target_id") or "")
            other_writers = [
                other for other_index, other in enumerate(rungs)
                if other_index != index
                and isinstance(other.get("action"), Mapping)
                and other["action"].get("instruction") == "SET"
                and other["action"].get("target_type") == "step"
                and str(other["action"].get("target_id") or "") == target
            ]
            if not other_writers:
                raise ProjectDesignDraftConflict(
                    "stale transition rung has no retained replacement writer"
                )
            remove_indices.add(index)
            continue
        current = conditions.get(index) or _json_clone(rung.get("condition"))
        if not isinstance(current, Mapping):
            raise ProjectDesignDraftConflict("stale step rung condition is unavailable")
        repaired = _prune_exact_stale_step_contact(
            current, [*match.group(2).split("/"), "sourceId"], stale_id
        )
        if repaired is None:
            raise ProjectDesignDraftConflict("stale step pruning would empty a rung condition")
        conditions[index] = repaired
    changes = [
        {"operation": "remove_item", "section": "ladder_rungs",
         "item_id": str(rungs[index].get("rung_id") or "")}
        for index in sorted(remove_indices)
    ]
    changes.extend(
        {"operation": "patch_item", "section": "ladder_rungs",
         "item_id": str(rungs[index].get("rung_id") or ""),
         "value": {"condition": condition}}
        for index, condition in sorted(conditions.items())
        if index not in remove_indices
    )
    if not changes:
        raise ProjectDesignDraftConflict("no stale step contact repair is applicable")
    return changes


def _control_network_contract_repair_changes(
    design: Mapping[str, Any], diagnostics: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Create missing internal networks and merge their permits into sole OUT rungs."""

    networks = [item for item in design.get("control_networks") or [] if isinstance(item, Mapping)]
    rungs = [item for item in design.get("ladder_rungs") or [] if isinstance(item, Mapping)]
    io_by_id = {
        str(item.get("signal_id") or ""): item
        for item in design.get("io_points") or [] if isinstance(item, Mapping)
    }
    used_ids = {str(item.get("network_id") or "") for item in networks}
    changes: list[dict[str, Any]] = []
    for diagnostic in diagnostics:
        code = diagnostic.get("code")
        received = diagnostic.get("received")
        if code == "control_network_out_cardinality_mismatch" and isinstance(received, Mapping):
            output = str(received.get("outputSignalId") or "")
            if (received.get("networkIds") or []) or len(received.get("outRungIds") or []) != 1:
                continue
            if (io_by_id.get(output) or {}).get("kind") != "internal_bit":
                raise ProjectDesignDraftConflict(
                    "a missing empty-permit network may be derived only for an internal bit"
                )
            base_id = f"cn_{output}"
            network_id = base_id
            suffix = 2
            while network_id in used_ids:
                network_id = f"{base_id}_{suffix}"
                suffix += 1
            used_ids.add(network_id)
            changes.append({
                "operation": "create_item", "section": "control_networks",
                "item_id": network_id,
                "value": {
                    "network_id": network_id,
                    "title": f"{output} internal calculation",
                    "output_signal_id": output,
                    "permit_all": [],
                },
            })
        elif code == "control_network_out_missing_effective_permits":
            match = re.fullmatch(
                r"/controlNetworks/(\d+)/permitAll",
                str(diagnostic.get("path") or ""),
            )
            if match is None or int(match.group(1)) >= len(networks):
                raise ProjectDesignDraftConflict("control network diagnostic is no longer current")
            network = networks[int(match.group(1))]
            output = str(network.get("output_signal_id") or "")
            writers = [
                rung for rung in rungs
                if isinstance(rung.get("action"), Mapping)
                and rung["action"].get("instruction") == "OUT"
                and rung["action"].get("target_type") == "signal"
                and str(rung["action"].get("target_id") or "") == output
            ]
            if len(writers) != 1 or not isinstance(writers[0].get("condition"), Mapping):
                raise ProjectDesignDraftConflict("control network does not have one retained OUT writer")
            condition = _json_clone(writers[0]["condition"])
            missing = [
                {"kind": "contact", "source_type": "signal",
                 "source_id": str(permit.get("signal_id") or ""),
                 "state": permit.get("state")}
                for permit in network.get("permit_all") or []
                if isinstance(permit, Mapping)
                and not _condition_has_exact_contact(
                    condition,
                    source_type="signal",
                    source_id=str(permit.get("signal_id") or ""),
                    state=str(permit.get("state") or ""),
                )
            ]
            if missing:
                terms = (
                    list(condition.get("terms") or [])
                    if condition.get("kind") == "all_of"
                    else [condition]
                )
                changes.append({
                    "operation": "patch_item", "section": "ladder_rungs",
                    "item_id": str(writers[0].get("rung_id") or ""),
                    "value": {"condition": {"kind": "all_of", "terms": [*terms, *missing]}},
                })
    if not changes:
        raise ProjectDesignDraftConflict("no control network contract repair is applicable")
    return changes


def _sequence_start_release_contract_repair_changes(
    design: Mapping[str, Any], diagnostics: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Bind automatic/manual sequence starts to their reviewed momentary inputs."""

    if not any(
        diagnostic.get("code") == "sequence_reset_release_missing"
        for diagnostic in diagnostics
    ):
        raise ProjectDesignDraftConflict("sequence start-release diagnostic is not current")
    bindings = design.get("index_acceptance_bindings")
    signals = bindings.get("signals") if isinstance(bindings, Mapping) else None
    modes = bindings.get("modes") if isinstance(bindings, Mapping) else None
    if not isinstance(signals, Mapping) or not isinstance(modes, Mapping):
        raise ProjectDesignDraftConflict(
            "reviewed automatic/manual start bindings are unavailable"
        )
    automatic_mode = str(modes.get("automatic") or "")
    manual_mode = str(modes.get("manual") or "")
    automatic_start = str(signals.get("automatic_start") or "")
    manual_start = str(signals.get("manual_start") or "")
    running = str(signals.get("running") or "")
    if (
        not all((automatic_mode, manual_mode, automatic_start, manual_start, running))
        or automatic_mode == manual_mode
    ):
        raise ProjectDesignDraftConflict(
            "reviewed automatic/manual start bindings are incomplete"
        )
    role_contracts = {
        # Continuous automatic sequencing is started by the retained run latch;
        # the momentary automatic button belongs only in that latch. Manual
        # sequencing remains directly press/release bound.
        automatic_mode: running,
        manual_mode: manual_start,
    }
    declared_signals = {
        str(item.get("signal_id") or "")
        for item in design.get("io_points") or [] if isinstance(item, Mapping)
    }
    if not set(role_contracts.values()) <= declared_signals:
        raise ProjectDesignDraftConflict("a reviewed sequence start signal is undeclared")
    changes: list[dict[str, Any]] = []
    seen_modes: set[str] = set()
    for sequence in design.get("sequences") or []:
        if not isinstance(sequence, Mapping):
            continue
        mode_id = str(sequence.get("mode_id") or "")
        start_signal = role_contracts.get(mode_id)
        if not start_signal:
            continue
        if mode_id in seen_modes:
            raise ProjectDesignDraftConflict(
                "a reviewed operating mode has multiple retained sequences"
            )
        seen_modes.add(mode_id)
        start_all = [_json_clone(item) for item in sequence.get("start_all") or []]
        reset_all = [_json_clone(item) for item in sequence.get("reset_all") or []]
        start_contact = {"signal_id": start_signal, "state": "on"}
        release_contact = {"signal_id": start_signal, "state": "off"}
        changed = False
        if start_contact not in start_all:
            start_all.append(start_contact)
            changed = True
        if release_contact not in reset_all:
            reset_all.append(release_contact)
            changed = True
        if changed:
            changes.append({
                "operation": "patch_item", "section": "sequences",
                "item_id": str(sequence.get("sequence_id") or ""),
                "value": {"start_all": start_all, "reset_all": reset_all},
            })
    if seen_modes != set(role_contracts):
        raise ProjectDesignDraftConflict(
            "each reviewed automatic/manual mode requires exactly one sequence"
        )
    if not changes:
        raise ProjectDesignDraftConflict(
            "reviewed sequence start-release contracts are already present"
        )
    return changes


def _index_mode_latch_repair_changes(
    design: Mapping[str, Any], diagnostics: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Build exact retained-item patches for stopped-only mode latches."""

    bindings = design.get("index_acceptance_bindings")
    signals = bindings.get("signals") if isinstance(bindings, Mapping) else None
    modes = bindings.get("modes") if isinstance(bindings, Mapping) else None
    if not isinstance(signals, Mapping) or not isinstance(modes, Mapping):
        raise ProjectDesignDraftConflict("index acceptance bindings are unavailable")
    running = str(signals.get("running") or "")
    selectors = {
        "automatic": str(signals.get("select_automatic") or ""),
        "manual": str(signals.get("select_manual") or ""),
    }
    targets = {
        "automatic": str(signals.get("automatic_mode") or ""),
        "manual": str(signals.get("manual_mode") or ""),
    }
    mode_ids = {
        "automatic": str(modes.get("automatic") or ""),
        "manual": str(modes.get("manual") or ""),
    }
    if not all((running, *selectors.values(), *targets.values(), *mode_ids.values())):
        raise ProjectDesignDraftConflict("index acceptance mode bindings are incomplete")
    mode_items = {
        str(item.get("mode_id") or ""): item
        for item in design.get("operating_modes") or []
        if isinstance(item, Mapping)
    }
    rungs = [item for item in design.get("ladder_rungs") or [] if isinstance(item, Mapping)]
    changes: list[dict[str, Any]] = []
    excluded = {running, *selectors.values(), *targets.values()}
    for role, other_role in (("automatic", "manual"), ("manual", "automatic")):
        mode_item = mode_items.get(mode_ids[role])
        if mode_item is None:
            raise ProjectDesignDraftConflict("bound operating mode was not found")
        writers = [
            rung for rung in rungs
            if isinstance(rung.get("action"), Mapping)
            and rung["action"].get("instruction") == "OUT"
            and rung["action"].get("target_type") == "signal"
            and str(rung["action"].get("target_id") or "") == targets[role]
        ]
        if len(writers) != 1:
            raise ProjectDesignDraftConflict(
                "bound mode signal requires exactly one retained OUT rung"
            )
        writer = writers[0]
        original = writer.get("condition")
        outer_terms = (
            list(original.get("terms") or [])
            if isinstance(original, Mapping) and original.get("kind") == "all_of"
            else [original]
        )
        preserved = [
            _json_clone(term)
            for term in outer_terms
            if isinstance(term, Mapping)
            and not _condition_mentions_any_signal(term, excluded)
        ]
        repaired_condition = {
            "kind": "all_of",
            "terms": [
                *preserved,
                {
                    "kind": "contact", "source_type": "signal",
                    "source_id": targets[other_role], "state": "off",
                },
                {
                    "kind": "any_of",
                    "terms": [
                        {
                            "kind": "all_of",
                            "terms": [
                                {
                                    "kind": "contact", "source_type": "signal",
                                    "source_id": selectors[role], "state": "on",
                                },
                                {
                                    "kind": "contact", "source_type": "signal",
                                    "source_id": running, "state": "off",
                                },
                            ],
                        },
                        {
                            "kind": "all_of",
                            "terms": [
                                {
                                    "kind": "contact", "source_type": "signal",
                                    "source_id": targets[role], "state": "on",
                                },
                                {
                                    "kind": "any_of",
                                    "terms": [
                                        {
                                            "kind": "contact", "source_type": "signal",
                                            "source_id": running, "state": "on",
                                        },
                                        {
                                            "kind": "contact", "source_type": "signal",
                                            "source_id": selectors[other_role], "state": "off",
                                        },
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ],
        }
        changes.extend([
            {
                "operation": "patch_item", "section": "operating_modes",
                "item_id": mode_ids[role],
                "value": {
                    "allowed_transitions": [
                        str(value) for value in mode_item.get("allowed_transitions") or []
                        if str(value) != mode_ids[other_role]
                    ]
                },
            },
            {
                "operation": "patch_item", "section": "ladder_rungs",
                "item_id": str(writer.get("rung_id") or ""),
                "value": {"condition": repaired_condition},
            },
        ])
    # Generic mode validation reports the exact retained writer rung in
    # relatedIds.  That is the authoritative repair target: operating-mode
    # activation declarations describe when a mode applies and need not name
    # the signal written by a separate derived stopped-mode rung.
    rung_by_id = {str(rung.get("rung_id") or ""): rung for rung in rungs}
    diagnosed_rung_ids = {
        str(related_id)
        for diagnostic in diagnostics
        if diagnostic.get("code") == "mode_activation_start_missing_stopped_condition"
        for related_id in diagnostic.get("relatedIds") or []
        if str(related_id) in rung_by_id
    }
    for diagnostic in diagnostics:
        if diagnostic.get("code") != "mode_activation_start_missing_stopped_condition":
            continue
        path_match = re.fullmatch(
            r"/ladderRungs/(\d+)/condition(?:/.*)?",
            str(diagnostic.get("path") or ""),
        )
        if path_match is None:
            continue
        rung_index = int(path_match.group(1))
        if rung_index < len(rungs):
            diagnosed_rung_ids.add(str(rungs[rung_index].get("rung_id") or ""))
    # Direct helper callers without checked diagnostics retain the semantic
    # fallback used by unit-level behavior tests. Production scoped repairs
    # always pass the current checked diagnostics above.
    if not diagnostics:
        activation_targets = {
            str(activation.get("signal_id") or "")
            for item in mode_items.values()
            if isinstance((activation := item.get("activation")), Mapping)
        }
        diagnosed_rung_ids = {
            str(rung.get("rung_id") or "")
            for rung in rungs
            if isinstance(rung.get("action"), Mapping)
            and rung["action"].get("instruction") == "OUT"
            and rung["action"].get("target_type") == "signal"
            and str(rung["action"].get("target_id") or "") in activation_targets
        }
    patches_by_rung = {
        str(change.get("item_id") or ""): change
        for change in changes
        if change.get("section") == "ladder_rungs"
    }
    for rung_id in sorted(diagnosed_rung_ids):
        writer = rung_by_id[rung_id]
        action = writer.get("action")
        if (
            not isinstance(action, Mapping)
            or action.get("instruction") != "OUT"
            or action.get("target_type") != "signal"
        ):
            raise ProjectDesignDraftConflict(
                "diagnosed mode activation rung is not a retained signal OUT writer"
            )
        target_signal = str(action.get("target_id") or "")
        prior_patch = patches_by_rung.get(rung_id)
        condition = (
            prior_patch["value"].get("condition")
            if prior_patch is not None
            else writer.get("condition")
        )
        if not target_signal or not isinstance(condition, Mapping):
            raise ProjectDesignDraftConflict("diagnosed mode condition is unavailable")
        repaired = _gate_mode_start_paths_for_stopped(
            condition, target_signal=target_signal, running_signal=running,
        )
        if prior_patch is not None:
            prior_patch["value"]["condition"] = repaired
        elif repaired != condition:
            patch = {
                "operation": "patch_item", "section": "ladder_rungs",
                "item_id": rung_id, "value": {"condition": repaired},
            }
            changes.append(patch)
            patches_by_rung[rung_id] = patch
    return changes


def _gate_mode_start_paths_for_stopped(
    condition: Mapping[str, Any], *, target_signal: str, running_signal: str,
) -> dict[str, Any]:
    """Put running OFF on each non-self mode activation path only."""

    if condition.get("kind") == "contact":
        if (
            condition.get("source_type") == "signal"
            and condition.get("source_id") == target_signal
            and condition.get("state") == "on"
        ):
            return _json_clone(condition)
        return {
            "kind": "all_of",
            "terms": [
                _json_clone(condition),
                {
                    "kind": "contact", "source_type": "signal",
                    "source_id": running_signal, "state": "off",
                },
            ],
        }
    terms = [term for term in condition.get("terms") or [] if isinstance(term, Mapping)]
    contains_self = _condition_mentions_any_signal(condition, {target_signal})
    if condition.get("kind") == "all_of" and not contains_self:
        if not _condition_mentions_any_signal(condition, {running_signal}):
            terms.append({
                "kind": "contact", "source_type": "signal",
                "source_id": running_signal, "state": "off",
            })
        return {"kind": "all_of", "terms": [_json_clone(term) for term in terms]}
    return {
        "kind": str(condition.get("kind")),
        "terms": [
            _gate_mode_start_paths_for_stopped(
                term, target_signal=target_signal, running_signal=running_signal,
            )
            for term in terms
        ],
    }


def _gate_automatic_start_paths(
    condition: Mapping[str, Any], *, start_signal: str, running_signal: str,
    arm_signal: str,
) -> dict[str, Any]:
    """Gate start-only branches without placing the arm in the running hold path."""

    kind = condition.get("kind")
    if kind == "contact":
        if (
            condition.get("source_type") == "signal"
            and condition.get("source_id") == start_signal
            and condition.get("state") == "on"
        ):
            return {
                "kind": "all_of",
                "terms": [
                    _json_clone(condition),
                    {
                        "kind": "contact", "source_type": "signal",
                        "source_id": arm_signal, "state": "on",
                    },
                ],
            }
        return _json_clone(condition)
    terms = [term for term in condition.get("terms") or [] if isinstance(term, Mapping)]
    has_start = _condition_mentions_any_signal(condition, {start_signal})
    has_running = _condition_mentions_any_signal(condition, {running_signal})
    if kind == "all_of" and has_start and not has_running:
        if not _condition_mentions_any_signal(condition, {arm_signal}):
            terms.append({
                "kind": "contact", "source_type": "signal",
                "source_id": arm_signal, "state": "on",
            })
        return {"kind": "all_of", "terms": [_json_clone(term) for term in terms]}
    return {
        "kind": str(kind),
        "terms": [
            _gate_automatic_start_paths(
                term, start_signal=start_signal, running_signal=running_signal,
                arm_signal=arm_signal,
            )
            for term in terms
        ],
    }


def _remove_stopped_gate_contacts(
    condition: Mapping[str, Any], stopped_signal_ids: set[str],
) -> dict[str, Any] | None:
    """Remove only diagnosed stopped-ON contacts while preserving Boolean shape."""

    kind = condition.get("kind")
    if kind == "contact":
        if (
            condition.get("source_type") == "signal"
            and str(condition.get("source_id") or "") in stopped_signal_ids
            and condition.get("state") == "on"
        ):
            return None
        return _json_clone(condition)
    if kind not in {"all_of", "any_of"}:
        raise ProjectDesignDraftConflict("diagnosed sequence condition is unsupported")
    original_terms = [
        term for term in condition.get("terms") or [] if isinstance(term, Mapping)
    ]
    repaired_terms = [
        repaired
        for term in original_terms
        if (repaired := _remove_stopped_gate_contacts(term, stopped_signal_ids))
        is not None
    ]
    if not repaired_terms:
        raise ProjectDesignDraftConflict(
            "stopped-gate removal would empty a sequence condition"
        )
    if kind == "any_of" and len(repaired_terms) != len(original_terms):
        raise ProjectDesignDraftConflict(
            "stopped-gate removal would make an OR branch unconditional"
        )
    if len(repaired_terms) == 1:
        return repaired_terms[0]
    return {"kind": str(kind), "terms": repaired_terms}


def _index_sequence_stopped_gate_repair_changes(
    design: Mapping[str, Any], diagnostics: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Patch diagnosed active sequence declarations and their exact SET rungs."""

    blocked: dict[str, set[str]] = {}
    active_mode_signals: dict[str, str] = {}
    contract_paths: dict[str, dict[str, set[str]]] = {}
    mode_activation_repairs: dict[str, tuple[str, set[str]]] = {}
    for diagnostic in diagnostics:
        if diagnostic.get("code") != (
            "index_acceptance_active_sequence_blocked_by_stopped_signal"
        ):
            continue
        received = diagnostic.get("received")
        items = received.get("blockedRungs") if isinstance(received, Mapping) else None
        for item in items or []:
            if not isinstance(item, Mapping):
                continue
            rung_id = str(item.get("rungId") or "")
            stopped_ids = {
                str(value) for value in item.get("stoppedSignalIds") or [] if str(value)
            }
            if rung_id and stopped_ids:
                blocked.setdefault(rung_id, set()).update(stopped_ids)
                active_signal_id = str(item.get("activeModeSignalId") or "")
                if active_signal_id:
                    prior_active = active_mode_signals.get(rung_id)
                    if prior_active is not None and prior_active != active_signal_id:
                        raise ProjectDesignDraftConflict(
                            "diagnosed active sequence rung has ambiguous mode ownership"
                        )
                    active_mode_signals[rung_id] = active_signal_id
            sequence_id = str(item.get("sequenceId") or "")
            for path in item.get("contractPaths") or []:
                path_value = str(path)
                if path_value.startswith("/operatingModes/"):
                    mode_id = str(item.get("modeId") or "")
                    active_mode_signal_id = str(item.get("activeModeSignalId") or "")
                    if mode_id and active_mode_signal_id:
                        existing = mode_activation_repairs.get(mode_id)
                        if existing is not None and existing[0] != active_mode_signal_id:
                            raise ProjectDesignDraftConflict(
                                "diagnosed mode activation repair is ambiguous"
                            )
                        mode_activation_repairs[mode_id] = (
                            active_mode_signal_id,
                            (existing[1] if existing is not None else set()) | stopped_ids,
                        )
                elif sequence_id and path_value:
                    contract_paths.setdefault(sequence_id, {}).setdefault(
                        path_value, set()
                    ).update(stopped_ids)
    if not blocked:
        raise ProjectDesignDraftConflict(
            "active-sequence stopped-gate diagnostic is not current"
        )
    rungs = {
        str(item.get("rung_id") or ""): item
        for item in design.get("ladder_rungs") or []
        if isinstance(item, Mapping)
    }
    changes: list[dict[str, Any]] = []
    operating_modes = {
        str(item.get("mode_id") or ""): item
        for item in design.get("operating_modes") or [] if isinstance(item, Mapping)
    }
    for mode_id, (active_signal_id, stopped_ids) in mode_activation_repairs.items():
        mode = operating_modes.get(mode_id)
        activation = mode.get("activation") if isinstance(mode, Mapping) else None
        if not (
            isinstance(activation, Mapping)
            and activation.get("state") == "on"
            and str(activation.get("signal_id") or "") in stopped_ids
        ):
            raise ProjectDesignDraftConflict(
                "diagnosed stopped-mode activation is no longer current"
            )
        changes.append({
            "operation": "patch_item", "section": "operating_modes",
            "item_id": mode_id,
            "value": {
                "activation": {"signal_id": active_signal_id, "state": "on"}
            },
        })
    sequences = [
        item for item in design.get("sequences") or [] if isinstance(item, Mapping)
    ]
    sequence_by_id = {
        str(item.get("sequence_id") or ""): item for item in sequences
    }
    for sequence_id, paths_to_ids in contract_paths.items():
        sequence = sequence_by_id.get(sequence_id)
        if sequence is None:
            raise ProjectDesignDraftConflict(
                "diagnosed active sequence declaration is no longer retained"
            )
        patch: dict[str, Any] = {}
        start_all = [_json_clone(item) for item in sequence.get("start_all") or []]
        steps = [_json_clone(item) for item in sequence.get("steps") or []]
        for path, stopped_ids in paths_to_ids.items():
            start_match = re.fullmatch(r"/sequences/\d+/startAll", path)
            step_match = re.fullmatch(r"/sequences/\d+/steps/(\d+)/completeAll", path)
            if start_match is not None:
                repaired_start = [
                    item for item in start_all
                    if not (
                        isinstance(item, Mapping)
                        and item.get("state") == "on"
                        and str(item.get("signal_id") or "") in stopped_ids
                    )
                ]
                if len(repaired_start) == len(start_all) or not repaired_start:
                    raise ProjectDesignDraftConflict(
                        "diagnosed stopped gate is unavailable in sequence start_all"
                    )
                start_all = repaired_start
                patch["start_all"] = start_all
                continue
            if step_match is None or int(step_match.group(1)) >= len(steps):
                raise ProjectDesignDraftConflict(
                    "diagnosed sequence contract path is no longer current"
                )
            step_index = int(step_match.group(1))
            complete_all = [
                item for item in steps[step_index].get("complete_all") or []
            ]
            repaired_complete = [
                item for item in complete_all
                if not (
                    isinstance(item, Mapping)
                    and item.get("state") == "on"
                    and str(item.get("signal_id") or "") in stopped_ids
                )
            ]
            if len(repaired_complete) == len(complete_all) or not repaired_complete:
                raise ProjectDesignDraftConflict(
                    "diagnosed stopped gate is unavailable in step complete_all"
                )
            steps[step_index]["complete_all"] = repaired_complete
            patch["steps"] = steps
        if patch:
            changes.append({
                "operation": "patch_item", "section": "sequences",
                "item_id": sequence_id, "value": patch,
            })
    for rung_id, stopped_ids in blocked.items():
        rung = rungs.get(rung_id)
        action = rung.get("action") if isinstance(rung, Mapping) else None
        condition = rung.get("condition") if isinstance(rung, Mapping) else None
        if not (
            isinstance(action, Mapping)
            and action.get("instruction") == "SET"
            and action.get("target_type") in {"step", "sequence_reset"}
            and isinstance(condition, Mapping)
        ):
            raise ProjectDesignDraftConflict(
                "diagnosed active sequence rung is no longer a retained sequence SET"
            )
        repaired = _remove_stopped_gate_contacts(condition, stopped_ids)
        if repaired is None:
            raise ProjectDesignDraftConflict(
                "stopped-gate removal would empty a retained sequence SET"
            )
        active_signal_id = active_mode_signals.get(rung_id)
        if active_signal_id and not _condition_has_exact_contact(
            repaired, source_type="signal", source_id=active_signal_id, state="on",
        ):
            repaired = {
                "kind": "all_of",
                "terms": [
                    repaired,
                    {
                        "kind": "contact", "source_type": "signal",
                        "source_id": active_signal_id, "state": "on",
                    },
                ],
            }
        if repaired != condition:
            changes.append({
                "operation": "patch_item", "section": "ladder_rungs",
                "item_id": rung_id, "value": {"condition": repaired},
            })
    if not changes:
        raise ProjectDesignDraftConflict("no active-sequence stopped gate remains")
    return changes


def _index_motion_run_gate_repair_changes(
    design: Mapping[str, Any], diagnostics: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Gate only diagnosed physical-motion OUT rungs with retained running."""

    bindings = design.get("index_acceptance_bindings")
    signals = bindings.get("signals") if isinstance(bindings, Mapping) else None
    if not isinstance(signals, Mapping):
        raise ProjectDesignDraftConflict("index acceptance bindings are unavailable")
    motion = str(signals.get("motion") or "")
    running = str(signals.get("running") or "")
    modes = bindings.get("modes")
    automatic_mode_id = str(modes.get("automatic") or "") if isinstance(modes, Mapping) else ""
    automatic_step_ids = {
        str(step.get("step_id"))
        for sequence in design.get("sequences") or []
        if isinstance(sequence, Mapping)
        and str(sequence.get("mode_id") or "") == automatic_mode_id
        for step in sequence.get("steps") or []
        if isinstance(step, Mapping) and step.get("step_id")
    }
    if not motion or not running or not automatic_step_ids:
        raise ProjectDesignDraftConflict("motion/running role bindings are unavailable")
    diagnostic = next(
        (
            item for item in diagnostics
            if item.get("code") == "index_acceptance_motion_not_gated_by_running"
        ),
        None,
    )
    if diagnostic is None:
        raise ProjectDesignDraftConflict("motion run-gate diagnostic is not current")
    diagnosed_ids = {
        str(item.get("rungId") or "")
        for item in (diagnostic.get("received") or {}).get("unguardedWriters") or []
        if isinstance(item, Mapping) and item.get("rungId")
    }

    def gate_automatic_steps(condition: Mapping[str, Any]) -> dict[str, Any]:
        if (
            condition.get("kind") == "contact"
            and condition.get("source_type") == "step"
            and str(condition.get("source_id") or "") in automatic_step_ids
            and condition.get("state") == "on"
        ):
            return {
                "kind": "all_of",
                "terms": [
                    _json_clone(condition),
                    {
                        "kind": "contact", "source_type": "signal",
                        "source_id": running, "state": "on",
                    },
                ],
            }
        if condition.get("kind") not in {"all_of", "any_of"}:
            return _json_clone(condition)
        return {
            **{
                key: _json_clone(value)
                for key, value in condition.items()
                if key != "terms"
            },
            "terms": [
                gate_automatic_steps(term) if isinstance(term, Mapping) else _json_clone(term)
                for term in condition.get("terms") or []
            ],
        }

    changes: list[dict[str, Any]] = []
    for rung in design.get("ladder_rungs") or []:
        if not isinstance(rung, Mapping) or str(rung.get("rung_id") or "") not in diagnosed_ids:
            continue
        action = rung.get("action")
        condition = rung.get("condition")
        if not (
            isinstance(action, Mapping)
            and action.get("instruction") == "OUT"
            and action.get("target_type") == "signal"
            and str(action.get("target_id") or "") == motion
            and isinstance(condition, Mapping)
        ):
            raise ProjectDesignDraftConflict(
                "diagnosed motion writer is no longer the retained physical-motion OUT rung"
            )
        repaired = gate_automatic_steps(condition)
        changes.append({
            "operation": "patch_item", "section": "ladder_rungs",
            "item_id": str(rung.get("rung_id")),
            "value": {
                "condition": repaired
            },
        })
    if not changes:
        raise ProjectDesignDraftConflict("no diagnosed motion writer remains unguarded")
    return changes


def _index_manual_stop_reset_repair_changes(
    design: Mapping[str, Any], diagnostics: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Prevent retained manual steps from resuming and make reset clear steps."""

    diagnostic = next(
        (
            item for item in diagnostics
            if item.get("code") == "index_acceptance_manual_stop_reset_can_resume_motion"
        ),
        None,
    )
    if diagnostic is None:
        diagnostic = next(
            (
                item for item in diagnostics
                if item.get("code") == "control_network_out_cardinality_mismatch"
                and str((item.get("received") or {}).get("outputSignalId") or "")
                in {"manual_move_pressed", "manual_running"}
            ),
            None,
        )
    if diagnostic is None:
        diagnostic = next(
            (
                item for item in diagnostics
                if item.get("code") == "sequence_reset_release_missing"
                and {"seq_auto", "seq_manual"}.intersection(
                    {str(value) for value in item.get("relatedIds") or []}
                )
            ),
            None,
        )
    if diagnostic is None:
        raise ProjectDesignDraftConflict("manual stop/reset diagnostic is not current")
    bindings = design.get("index_acceptance_bindings")
    signals = bindings.get("signals") if isinstance(bindings, Mapping) else None
    modes = bindings.get("modes") if isinstance(bindings, Mapping) else None
    if not isinstance(signals, Mapping) or not isinstance(modes, Mapping):
        raise ProjectDesignDraftConflict("index acceptance bindings are unavailable")
    motion = str(signals.get("motion") or "")
    stop = str(signals.get("stop") or "")
    reset = str(signals.get("reset") or "")
    manual_start = str(signals.get("manual_start") or "")
    manual_mode_signal = str(signals.get("manual_mode") or "")
    manual_mode_id = str(modes.get("manual") or "")
    automatic_mode_id = str(modes.get("automatic") or "")
    manual_running = next(
        (
            str(point.get("signal_id"))
            for point in design.get("io_points") or []
            if isinstance(point, Mapping)
            and str(point.get("signal_id") or "") == "manual_running"
        ),
        "",
    )
    sequences = {
        str(sequence.get("sequence_id") or ""): sequence
        for sequence in design.get("sequences") or []
        if isinstance(sequence, Mapping)
        and str(sequence.get("mode_id") or "") in {automatic_mode_id, manual_mode_id}
    }
    manual_steps = {
        str(step.get("step_id") or "")
        for sequence in sequences.values()
        if str(sequence.get("mode_id") or "") == manual_mode_id
        for step in sequence.get("steps") or []
        if isinstance(step, Mapping) and step.get("step_id")
    }
    manual_terminal_steps = [
        str(steps[-1].get("step_id") or "")
        for sequence in sequences.values()
        if str(sequence.get("mode_id") or "") == manual_mode_id
        and (steps := [
            step for step in sequence.get("steps") or []
            if isinstance(step, Mapping)
        ])
    ]
    alarm_ids = bindings.get("alarms")
    selected_alarm_ids = (
        {str(value) for value in alarm_ids.values()}
        if isinstance(alarm_ids, Mapping) else set()
    )
    alarm_latches = [
        str(alarm.get("latch_signal_id") or "")
        for alarm in design.get("alarms") or []
        if isinstance(alarm, Mapping)
        and str(alarm.get("alarm_id") or "") in selected_alarm_ids
    ]
    if not all(
        (motion, stop, reset, manual_start, manual_mode_signal, manual_running,
         manual_mode_id, automatic_mode_id, manual_steps, manual_terminal_steps,
         sequences, alarm_latches)
    ):
        raise ProjectDesignDraftConflict("manual stop/reset repair identities are unavailable")

    received = diagnostic.get("received")
    received_map = received if isinstance(received, Mapping) else {}
    unguarded_ids = {
        str(item.get("rungId") or "")
        for item in (received_map.get("unguardedMotionWriters") or [])
        if isinstance(item, Mapping)
    }

    def gate_manual_steps(condition: Mapping[str, Any]) -> dict[str, Any]:
        if (
            condition.get("kind") == "all_of"
            and any(
                isinstance(term, Mapping)
                and term.get("kind") == "contact"
                and term.get("source_type") == "step"
                and str(term.get("source_id") or "") in manual_steps
                and term.get("state") == "on"
                for term in condition.get("terms") or []
            )
            and _condition_has_exact_contact(
                condition,
                source_type="signal",
                source_id=manual_running,
                state="on",
            )
        ):
            return _json_clone(condition)
        if (
            condition.get("kind") == "contact"
            and condition.get("source_type") == "step"
            and str(condition.get("source_id") or "") in manual_steps
            and condition.get("state") == "on"
        ):
            return {
                "kind": "all_of",
                "terms": [
                    _json_clone(condition),
                    {
                        "kind": "contact", "source_type": "signal",
                        "source_id": manual_running, "state": "on",
                    },
                ],
            }
        if condition.get("kind") not in {"all_of", "any_of"}:
            return _json_clone(condition)
        return {
            **{key: _json_clone(value) for key, value in condition.items() if key != "terms"},
            "terms": [
                gate_manual_steps(term) if isinstance(term, Mapping) else _json_clone(term)
                for term in condition.get("terms") or []
            ],
        }

    rungs = {
        str(rung.get("rung_id") or ""): rung
        for rung in design.get("ladder_rungs") or [] if isinstance(rung, Mapping)
    }
    changes: list[dict[str, Any]] = []
    for rung_id in unguarded_ids:
        rung = rungs.get(rung_id)
        action = rung.get("action") if isinstance(rung, Mapping) else None
        condition = rung.get("condition") if isinstance(rung, Mapping) else None
        if not (
            isinstance(action, Mapping)
            and action.get("instruction") == "OUT"
            and action.get("target_type") == "signal"
            and str(action.get("target_id") or "") == motion
            and isinstance(condition, Mapping)
        ):
            raise ProjectDesignDraftConflict("diagnosed manual motion writer changed")
        changes.append({
            "operation": "patch_item", "section": "ladder_rungs",
            "item_id": rung_id,
            "value": {"condition": gate_manual_steps(condition)},
        })

    run_rung_id = "rung_manual_move_oneshot"
    run_rung = rungs.get(run_rung_id)
    run_action = run_rung.get("action") if isinstance(run_rung, Mapping) else None
    if not (
        isinstance(run_action, Mapping)
        and run_action.get("instruction") == "OUT"
        and run_action.get("target_type") == "signal"
        and str(run_action.get("target_id") or "") in {"manual_move_pressed", manual_running}
    ):
        raise ProjectDesignDraftConflict("manual retained-run rung is unavailable")
    changes.append({
        "operation": "patch_item", "section": "ladder_rungs",
        "item_id": run_rung_id,
        "value": {
            "description": (
                "Manual run permit: latch one requested move; normal stop, reset, "
                "fault, or terminal manual step clears it."
            ),
            "action": {
                "instruction": "OUT", "target_type": "signal",
                "target_id": manual_running,
            },
            "condition": {
                "kind": "all_of",
                "terms": [
                    {"kind": "contact", "source_type": "signal", "source_id": stop, "state": "off"},
                    {"kind": "contact", "source_type": "signal", "source_id": reset, "state": "off"},
                    {"kind": "contact", "source_type": "signal", "source_id": manual_mode_signal, "state": "on"},
                    *[
                        {"kind": "contact", "source_type": "signal", "source_id": latch, "state": "off"}
                        for latch in alarm_latches
                    ],
                    *[
                        {"kind": "contact", "source_type": "step", "source_id": step, "state": "off"}
                        for step in manual_terminal_steps
                    ],
                    {
                        "kind": "any_of",
                        "terms": [
                            {"kind": "contact", "source_type": "signal", "source_id": manual_start, "state": "on"},
                            {"kind": "contact", "source_type": "signal", "source_id": manual_running, "state": "on"},
                        ],
                    },
                ],
            },
        },
    })
    manual_networks = [
        network
        for network in design.get("control_networks") or []
        if isinstance(network, Mapping)
        and str(network.get("output_signal_id") or "")
        in {"manual_move_pressed", manual_running}
    ]
    if len(manual_networks) != 1:
        raise ProjectDesignDraftConflict("manual retained-run control network is ambiguous")
    manual_network = manual_networks[0]
    changes.append({
        "operation": "patch_item", "section": "control_networks",
        "item_id": str(manual_network.get("network_id") or ""),
        "value": {
            "title": "Manual retained run permit",
            "output_signal_id": manual_running,
            "permit_all": [
                {"signal_id": stop, "state": "off"},
                {"signal_id": reset, "state": "off"},
                {"signal_id": manual_mode_signal, "state": "on"},
                *[
                    {"signal_id": latch, "state": "off"}
                    for latch in alarm_latches
                ],
            ],
        },
    })

    reset_rung_ids = {
        automatic_mode_id: "rung_seq_auto_reset",
        manual_mode_id: "rung_seq_manual_reset_explicit",
    }
    for sequence_id, sequence in sequences.items():
        reset_conditions = [{"signal_id": reset, "state": "on"}]
        if str(sequence.get("mode_id") or "") == manual_mode_id:
            reset_conditions.append({"signal_id": manual_start, "state": "off"})
        changes.append({
            "operation": "patch_item", "section": "sequences",
            "item_id": sequence_id,
            "value": {"reset_all": reset_conditions},
        })
        rung_id = reset_rung_ids.get(str(sequence.get("mode_id") or ""))
        rung = rungs.get(str(rung_id or ""))
        action = rung.get("action") if isinstance(rung, Mapping) else None
        if not (
            rung_id
            and isinstance(action, Mapping)
            and action.get("instruction") == "SET"
            and action.get("target_type") == "sequence_reset"
            and str(action.get("target_id") or "") == sequence_id
        ):
            raise ProjectDesignDraftConflict("explicit sequence reset rung is unavailable")
        changes.append({
            "operation": "patch_item", "section": "ladder_rungs",
            "item_id": rung_id,
            "value": {
                "description": "Operator reset clears the retained sequence without requiring an alarm.",
                "condition": (
                    {
                        "kind": "all_of",
                        "terms": [
                            {
                                "kind": "contact", "source_type": "signal",
                                "source_id": condition["signal_id"],
                                "state": condition["state"],
                            }
                            for condition in reset_conditions
                        ],
                    }
                    if len(reset_conditions) > 1
                    else {
                        "kind": "contact", "source_type": "signal",
                        "source_id": reset, "state": "on",
                    }
                ),
            },
        })
    return changes


def _remove_exact_signal_contacts(
    condition: Mapping[str, Any], contacts: set[tuple[str, str]],
) -> dict[str, Any] | None:
    """Remove exact signal/state contacts without broadening an OR branch."""

    kind = condition.get("kind")
    if kind == "contact":
        if (
            condition.get("source_type") == "signal"
            and (str(condition.get("source_id") or ""), str(condition.get("state") or ""))
            in contacts
        ):
            return None
        return _json_clone(condition)
    if kind not in {"all_of", "any_of"}:
        raise ProjectDesignDraftConflict("diagnosed timeout reset condition is unsupported")
    original_terms = [
        term for term in condition.get("terms") or [] if isinstance(term, Mapping)
    ]
    repaired_terms = [
        repaired
        for term in original_terms
        if (repaired := _remove_exact_signal_contacts(term, contacts)) is not None
    ]
    if not repaired_terms:
        raise ProjectDesignDraftConflict(
            "timeout reset-gate removal would empty a retained condition"
        )
    if kind == "any_of" and len(repaired_terms) != len(original_terms):
        raise ProjectDesignDraftConflict(
            "timeout reset-gate removal would make an OR branch unconditional"
        )
    if len(repaired_terms) == 1:
        return repaired_terms[0]
    return {"kind": str(kind), "terms": repaired_terms}


def _index_timeout_reset_gate_repair_changes(
    design: Mapping[str, Any], diagnostics: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Remove only diagnosed stale timeout-done OFF gates from recovery paths."""

    diagnostic = next(
        (
            item for item in diagnostics
            if item.get("code") == (
                "index_acceptance_timeout_reset_blocked_by_done_mirror"
            )
        ),
        None,
    )
    received = diagnostic.get("received") if isinstance(diagnostic, Mapping) else None
    if not isinstance(received, Mapping):
        raise ProjectDesignDraftConflict("timeout reset diagnostic is not current")
    done_signals = {
        str(value) for value in received.get("timeoutDoneSignalIds") or [] if str(value)
    }
    recovery_ids = {
        str(item.get("recoveryId") or "")
        for item in received.get("unsafeRecoveries") or []
        if isinstance(item, Mapping) and item.get("recoveryId")
    }
    rung_ids = {
        str(item.get("rungId") or "")
        for item in received.get("unsafeRungs") or []
        if isinstance(item, Mapping) and item.get("rungId")
    }
    if not done_signals or not (recovery_ids or rung_ids):
        raise ProjectDesignDraftConflict("timeout reset repair targets are unavailable")
    contacts = {(signal_id, "off") for signal_id in done_signals}
    changes: list[dict[str, Any]] = []
    recoveries = {
        str(item.get("recovery_id") or ""): item
        for item in design.get("recovery_rules") or [] if isinstance(item, Mapping)
    }
    for recovery_id in sorted(recovery_ids):
        recovery = recoveries.get(recovery_id)
        if recovery is None:
            raise ProjectDesignDraftConflict(
                "diagnosed timeout recovery is no longer retained"
            )
        original = [
            _json_clone(item) for item in recovery.get("when_all") or []
            if isinstance(item, Mapping)
        ]
        repaired = [
            item for item in original
            if (str(item.get("signal_id") or ""), str(item.get("state") or ""))
            not in contacts
        ]
        if not repaired or repaired == original:
            raise ProjectDesignDraftConflict(
                "diagnosed timeout recovery gate is no longer present"
            )
        changes.append({
            "operation": "patch_item", "section": "recovery_rules",
            "item_id": recovery_id, "value": {"when_all": repaired},
        })
    rungs = {
        str(item.get("rung_id") or ""): item
        for item in design.get("ladder_rungs") or [] if isinstance(item, Mapping)
    }
    for rung_id in sorted(rung_ids):
        rung = rungs.get(rung_id)
        condition = rung.get("condition") if isinstance(rung, Mapping) else None
        if not isinstance(condition, Mapping):
            raise ProjectDesignDraftConflict(
                "diagnosed timeout reset rung is no longer retained"
            )
        repaired = _remove_exact_signal_contacts(condition, contacts)
        if repaired is None or repaired == condition:
            raise ProjectDesignDraftConflict(
                "diagnosed timeout reset rung gate is no longer present"
            )
        changes.append({
            "operation": "patch_item", "section": "ladder_rungs",
            "item_id": rung_id, "value": {"condition": repaired},
        })
    if not changes:
        raise ProjectDesignDraftConflict("no timeout reset-gate repair is applicable")
    return changes


def _index_start_rearm_repair_changes(design: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build a retained-running-rung repair plus one stable release-arm writer."""

    bindings = design.get("index_acceptance_bindings")
    signals = bindings.get("signals") if isinstance(bindings, Mapping) else None
    if not isinstance(signals, Mapping):
        raise ProjectDesignDraftConflict("index acceptance bindings are unavailable")
    start = str(signals.get("automatic_start") or "")
    running = str(signals.get("running") or "")
    if not start or not running:
        raise ProjectDesignDraftConflict("automatic start bindings are incomplete")
    io_points = [item for item in design.get("io_points") or [] if isinstance(item, Mapping)]
    by_signal = {str(item.get("signal_id") or ""): item for item in io_points}
    arm_id = "automatic_start_release_arm"
    arm = by_signal.get(arm_id)
    if arm is not None and arm.get("kind") != "internal_bit":
        raise ProjectDesignDraftConflict("stable release-arm ID is not an internal bit")
    used_addresses = {str(item.get("address") or "") for item in io_points}
    if arm is None:
        input_bytes = {
            (match.group(1), int(match.group(2), 16) // 8)
            for item in io_points
            if item.get("kind") == "digital_input"
            and (
                match := re.fullmatch(
                    r"M(\d{3,4})([0-9A-F])", str(item.get("address") or "")
                )
            ) is not None
        }
        arm_address = next(
            (
                f"M{word:04d}{bit:X}"
                for word in range(200, 10_000)
                for bit in range(16)
                if f"M{word:04d}{bit:X}" not in used_addresses
                and (f"{word:04d}", bit // 8) not in input_bytes
            ),
            None,
        )
        if arm_address is None:
            raise ProjectDesignDraftConflict("no internal-bit address is available")
    rungs = [item for item in design.get("ladder_rungs") or [] if isinstance(item, Mapping)]
    running_writers = [
        rung for rung in rungs
        if isinstance(rung.get("action"), Mapping)
        and rung["action"].get("instruction") == "OUT"
        and rung["action"].get("target_type") == "signal"
        and str(rung["action"].get("target_id") or "") == running
    ]
    if len(running_writers) != 1:
        raise ProjectDesignDraftConflict(
            "bound running signal requires exactly one retained OUT rung"
        )
    running_rung = running_writers[0]
    if not isinstance(running_rung.get("condition"), Mapping):
        raise ProjectDesignDraftConflict("running rung condition is unavailable")
    arm_rung_id = "automatic_start_release_arm_out"
    arm_writers = [
        rung for rung in rungs
        if str(rung.get("rung_id") or "") == arm_rung_id
    ]
    if arm_writers and not (
        isinstance(arm_writers[0].get("action"), Mapping)
        and arm_writers[0]["action"].get("instruction") == "OUT"
        and arm_writers[0]["action"].get("target_type") == "signal"
        and arm_writers[0]["action"].get("target_id") == arm_id
    ):
        raise ProjectDesignDraftConflict("stable release-arm rung ID is already in use")
    changes: list[dict[str, Any]] = []
    if arm is None:
        changes.append({
            "operation": "create_item", "section": "io_points", "item_id": arm_id,
            "value": {
                "signal_id": arm_id, "symbol": "AUTOMATIC_START_RELEASE_ARM",
                "address": arm_address, "kind": "internal_bit",
                "description": "Automatic start release arm", "normal_state": "off",
                "safe_state": "off",
            },
        })
    changes.extend([
        {
            "operation": "patch_item", "section": "ladder_rungs",
            "item_id": str(running_rung.get("rung_id") or ""),
            "value": {"condition": _gate_automatic_start_paths(
                running_rung["condition"], start_signal=start,
                running_signal=running, arm_signal=arm_id,
            )},
        },
        {
            "operation": "patch_item" if arm_writers else "create_item",
            "section": "ladder_rungs", "item_id": arm_rung_id,
            "value": {
                "rung_id": arm_rung_id,
                "description": "Arm automatic start only after button release",
                "condition": {
                    "kind": "contact", "source_type": "signal",
                    "source_id": start, "state": "off",
                },
                "action": {
                    "instruction": "OUT", "target_type": "signal", "target_id": arm_id,
                },
            },
        },
    ])
    return changes


class ProjectDesignDraftService:
    """Agent-facing operations over one conversation-bound design draft."""

    def __init__(self, repository: ProjectDesignDraftRepository) -> None:
        self._repository = repository

    def retained_requirement_message_ids(self, conversation_id: str) -> set[str]:
        """Return message provenance still referenced by the current durable draft."""

        draft = self._repository.get(conversation_id)
        if draft is None:
            return set()
        narrative = draft.document.get("narrative")
        sources = narrative.get("requirement_sources", []) if isinstance(
            narrative, dict
        ) else []
        return {
            str(message_id)
            for source in sources
            if isinstance(source, dict)
            and isinstance(
                message_id := source.get("message_id") or source.get("messageId"),
                str,
            )
        }

    def retained_narrative(
        self, conversation_id: str, *, brief_digest: str
    ) -> dict[str, Any] | None:
        """Return same-brief retained prose for an authority-safe continuation."""

        draft = self._repository.get(conversation_id)
        if draft is None or draft.brief_digest != brief_digest:
            return None
        narrative = draft.document.get("narrative")
        if not isinstance(narrative, dict) or narrative.get("status") != "ready_for_review":
            return None
        return _json_clone(narrative)

    def retained_narrative_context(
        self, conversation_id: str, *, brief_digest: str
    ) -> dict[str, Any] | None:
        draft = self._repository.get(conversation_id)
        if draft is None:
            return None
        narrative_value = draft.document.get("narrative")
        if not isinstance(narrative_value, dict) or narrative_value.get("status") != "ready_for_review":
            return None
        narrative = _json_clone(narrative_value)
        boundary = self._repository.narrative_adoption_boundary_message_id(
            conversation_id,
            brief_digest=draft.brief_digest,
            retained_narrative=narrative,
        )
        brief_relation = "current"
        if draft.brief_digest != brief_digest:
            if boundary is None:
                return None
            structured_change = self._repository.has_structured_brief_after_message(
                conversation_id, boundary
            )
            if structured_change is not False:
                return None
            brief_relation = "supplemental_only_drift"
        return {
            "narrative": narrative,
            "adoption_boundary_message_id": boundary,
            "brief_relation": brief_relation,
        }

    @staticmethod
    def tool_catalog() -> list[AgentToolDefinitionV1]:
        section_enum = list(_EDITABLE_SECTIONS)
        design_schema = ProjectPlanNativeDesignV1.model_json_schema(by_alias=False)
        technical_schema = ProjectPlanTechnicalConfigurationV1.model_json_schema(by_alias=False)
        definitions = {
            **design_schema.get("$defs", {}),
            **technical_schema.get("$defs", {}),
        }
        technical_schema.pop("$defs", None)

        def referenced_definitions(schema: Mapping[str, Any]) -> dict[str, Any]:
            names: set[str] = set()

            def visit(value: Any) -> None:
                if isinstance(value, dict):
                    reference = value.get("$ref")
                    if isinstance(reference, str) and reference.startswith("#/$defs/"):
                        name = reference.removeprefix("#/$defs/")
                        if name not in names and name in definitions:
                            names.add(name)
                            visit(definitions[name])
                    for child in value.values():
                        visit(child)
                elif isinstance(value, list):
                    for child in value:
                        visit(child)

            visit(schema)
            return {name: _json_clone(definitions[name]) for name in sorted(names)}

        narrative_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "requirement_sources",
                "status",
                "project_name",
                "summary",
                "objective",
                "user_requirements",
                "design_proposals",
                "scope",
                "assumptions",
                "open_questions",
                "hardware_and_network",
                "io_and_devices",
                "operating_modes",
                "sequence",
                "safety_invariants",
                "alarms_and_recovery",
                "program_structure",
                "validation_plan",
                "not_applicable",
            ],
            "properties": {
                "requirement_sources": {"type": "array"},
                "status": {"type": "string", "enum": ["ready_for_review"]},
                "project_name": {"type": "string", "minLength": 1},
                "summary": {"type": "string", "minLength": 1},
                "objective": {"type": "string", "minLength": 1},
                **{
                    name: {"type": "array", "items": {"type": "string"}}
                    for name in (
                        "user_requirements",
                        "design_proposals",
                        "scope",
                        "assumptions",
                        "open_questions",
                        "hardware_and_network",
                        "io_and_devices",
                        "operating_modes",
                        "sequence",
                        "safety_invariants",
                        "alarms_and_recovery",
                        "program_structure",
                        "validation_plan",
                    )
                },
                "not_applicable": {"type": "array"},
            },
        }

        def operation_schema(
            operation: str,
            section: str,
            *,
            value_schema: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            properties: dict[str, Any] = {
                "operation": {"type": "string", "enum": [operation]},
                "section": {"type": "string", "enum": [section]},
            }
            required = ["operation", "section"]
            if operation in {
                "create_item",
                "patch_item",
                "upsert_item",
                "remove_item",
                "move_item",
            }:
                properties["item_id"] = {
                    "type": "string",
                    "pattern": "^[a-z][a-z0-9_]*$",
                    "maxLength": 80,
                }
                required.append("item_id")
            if value_schema is not None:
                properties["value"] = value_schema
                required.append("value")
            return {
                "type": "object",
                "additionalProperties": False,
                "required": required,
                "properties": properties,
            }

        design_properties = design_schema["properties"]
        change_variants = [
            operation_schema(
                "replace_section",
                "narrative",
                value_schema=narrative_schema,
            ),
            operation_schema(
                "replace_section",
                "technical_configuration",
                value_schema=technical_schema,
            ),
            operation_schema(
                "replace_section",
                "index_acceptance_bindings",
                value_schema=ProjectPlanIndexAcceptanceBindingsV1.model_json_schema(by_alias=False),
            ),
        ]
        collection_tools: list[AgentToolDefinitionV1] = []
        ladder_patch_schema: dict[str, Any] | None = None
        ladder_schema_definitions: dict[str, Any] = {}
        for section in _COLLECTIONS:
            item_schema = _json_clone(design_properties[section]["items"])
            # Existing entities are edited as durable patches. Requiring the
            # model to echo every unchanged field turns a one-field repair into
            # a lossy full replacement and is especially brittle for local
            # models. New items still pass the complete Pydantic collection
            # contract after merge, so incomplete creates are rejected.
            patch_schema = _json_clone(item_schema)
            root_ref = patch_schema.get("$ref")
            if isinstance(root_ref, str) and root_ref.startswith("#/$defs/"):
                root_name = root_ref.rsplit("/", 1)[-1]
                root_definition = (design_schema.get("$defs") or {}).get(root_name)
                if isinstance(root_definition, dict):
                    patch_schema = _json_clone(root_definition)
            patch_schema.pop("required", None)
            patch_schema["minProperties"] = 1
            if section == "ladder_rungs":
                ladder_patch_schema = _json_clone(patch_schema)
                ladder_schema_definitions = referenced_definitions(item_schema)
            create_schema = operation_schema(
                "create_item",
                section,
                value_schema=item_schema,
            )
            patch_item_schema = operation_schema(
                "patch_item",
                section,
                value_schema=patch_schema,
            )
            remove_schema = operation_schema("remove_item", section)
            move_schema = operation_schema("move_item", section)
            move_schema["required"].append("before_item_id")
            move_schema["properties"]["before_item_id"] = {
                "type": "string",
                "pattern": "^[a-z][a-z0-9_]*$",
                "maxLength": 80,
            }
            # The shared workbench needs the section-qualified variants.
            # Clone before adapting the same schemas for legacy section tools.
            change_variants.extend(_json_clone(schema) for schema in (
                create_schema, patch_item_schema, remove_schema,
            ))
            if section == "ladder_rungs":
                change_variants.append(_json_clone(move_schema))
            for schema in (create_schema, patch_item_schema, remove_schema):
                schema["required"].remove("section")
                schema["properties"].pop("section")
            move_schema["required"].remove("section")
            move_schema["properties"].pop("section")
            operation_variants = [create_schema, patch_item_schema, remove_schema]
            if section == "ladder_rungs":
                operation_variants.append(move_schema)
            collection_tools.append(
                AgentToolDefinitionV1(
                    tool_name=f"design.edit_{section}",
                    description=(
                        f"Create, patch, or remove at most {_MAX_SECTION_CHANGES} {section} items "
                        "in the retained draft. Use create_item with every schema-required field for "
                        "a new item. Use patch_item only for an existing item; its value contains only "
                        "changed fields and omitted fields are preserved. This tool cannot replace "
                        "any other collection."
                        + (
                            " Use move_item with before_item_id to change ladder execution "
                            "order without rewriting the rung."
                            if section == "ladder_rungs"
                            else ""
                        )
                    ),
                    parameters_json_schema={
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["expected_revision", "changes"],
                        "properties": {
                            "expected_revision": {"type": "integer", "minimum": 0},
                            "changes": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": _MAX_SECTION_CHANGES,
                                "items": {"oneOf": operation_variants},
                            },
                        },
                        "$defs": referenced_definitions(item_schema),
                    },
                )
            )
        assert ladder_patch_schema is not None
        ladder_condition_schema = _json_clone(
            ladder_patch_schema["properties"]["condition"]
        )
        return [
            AgentToolDefinitionV1(
                tool_name="design.inspect",
                description=(
                    "Inspect the durable new-project design draft, its revision, completed "
                    "sections, remaining work, exact validation diagnostics, and the bounded "
                    "revision range available to design.read. Call this before editing and "
                    "after a revision conflict."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {},
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.read",
                description=(
                    "Read exact selected sections from the current durable design draft by "
                    "default. Set revision to an integer for that immutable same-conversation "
                    "revision, or to latest_published for the newest published base. Set "
                    "published_plan_digest to a Host-observed reviewedPlanDigest for that exact "
                    "same-conversation published base; do not combine it with revision. Historical "
                    "reads are read-only and currentRevision remains the edit authority."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["sections"],
                    "properties": {
                        "sections": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": len(section_enum),
                            "uniqueItems": True,
                            "items": {"type": "string", "enum": section_enum},
                        },
                        "revision": {
                            "oneOf": [
                                {"type": "integer", "minimum": 0},
                                {"type": "string", "enum": ["latest_published"]},
                            ]
                        },
                        "published_plan_digest": {
                            "type": "string",
                            "pattern": "^[0-9a-f]{64}$",
                        },
                    },
                    "not": {
                        "required": ["revision", "published_plan_digest"],
                    },
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.edit",
                description=(
                    "Atomically edit a small batch in the durable draft. Replace only the "
                    "narrative, technical configuration, or index acceptance bindings; collection entities must use "
                    "create_item/patch_item/remove_item so later failures never regenerate a whole "
                    "collection. A rejected edit keeps the prior revision."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["expected_revision", "changes"],
                    "properties": {
                        "expected_revision": {"type": "integer", "minimum": 0},
                        "changes": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"oneOf": change_variants},
                        },
                    },
                    "$defs": _json_clone(design_schema.get("$defs") or {}),
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.edit_io_safe_states",
                description=(
                    "Patch only safe_state on existing io_points. Use this for broad "
                    "safe-state corrections without resending complete I/O objects."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["expected_revision", "changes"],
                    "properties": {
                        "expected_revision": {"type": "integer", "minimum": 0},
                        "changes": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 512,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["signal_id", "safe_state"],
                                "properties": {
                                    "signal_id": {
                                        "type": "string",
                                        "pattern": "^[a-z][a-z0-9_]*$",
                                        "maxLength": 80,
                                    },
                                    "safe_state": {
                                        "type": "string",
                                        "enum": ["on", "off", "not_applicable"],
                                    },
                                },
                            },
                        },
                    },
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.patch_ladder_rung",
                description=(
                    "Patch one existing ladder rung in place. Supply only the condition, action, "
                    "or description fields that must change; every omitted field is preserved. "
                    "Use this instead of a collection edit for a diagnostic that identifies one rung."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["expected_revision", "rung_id", "patch"],
                    "properties": {
                        "expected_revision": {"type": "integer", "minimum": 0},
                        "rung_id": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]*$",
                            "maxLength": 80,
                        },
                        "patch": ladder_patch_schema,
                    },
                    "$defs": ladder_schema_definitions,
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.set_ladder_increment_action",
                description=(
                    "Create a missing completion-counter rung or repair an existing one as a "
                    "positive-edge INCP write. Supply its exact automatic/manual completion "
                    "condition and description. The Host constructs the PLC action and chooses "
                    "create versus patch atomically from the retained rung ID."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "expected_revision",
                        "rung_id",
                        "destination_address",
                        "condition",
                        "description",
                    ],
                    "properties": {
                        "expected_revision": {"type": "integer", "minimum": 0},
                        "rung_id": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]*$",
                            "maxLength": 80,
                        },
                        "destination_address": {
                            "type": "string",
                            "pattern": "^D\\d{5}$",
                        },
                        "condition": ladder_condition_schema,
                        "description": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 500,
                        },
                    },
                    "$defs": ladder_schema_definitions,
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.sync_sequence_start_rung",
                description=(
                    "Create or repair one sequence start SET rung from the retained "
                    "sequence, mode, and start contracts. The Host copies the exact "
                    "reviewed conditions and preserves unrelated ladder work."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["expected_revision", "rung_id", "sequence_id"],
                    "properties": {
                        "expected_revision": {"type": "integer", "minimum": 0},
                        "rung_id": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]*$",
                            "maxLength": 80,
                        },
                        "sequence_id": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]*$",
                            "maxLength": 80,
                        },
                    },
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.sync_sequence_transition_rung",
                description=(
                    "Create or repair one sequence transition SET rung from the retained "
                    "source-step completion and mode contracts. The Host selects the next "
                    "step or sequence reset target deterministically."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "expected_revision",
                        "rung_id",
                        "sequence_id",
                        "source_step_id",
                    ],
                    "properties": {
                        "expected_revision": {"type": "integer", "minimum": 0},
                        "rung_id": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]*$",
                            "maxLength": 80,
                        },
                        "sequence_id": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]*$",
                            "maxLength": 80,
                        },
                        "source_step_id": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]*$",
                            "maxLength": 80,
                        },
                    },
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.sync_sequence_reset_rung",
                description=(
                    "Create or repair one explicit sequence-reset SET rung from the "
                    "retained sequence reset_all contract."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["expected_revision", "rung_id", "sequence_id"],
                    "properties": {
                        "expected_revision": {"type": "integer", "minimum": 0},
                        "rung_id": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]*$",
                            "maxLength": 80,
                        },
                        "sequence_id": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]*$",
                            "maxLength": 80,
                        },
                    },
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.set_ladder_counter_reset_action",
                description=(
                    "Create or repair one MOV 0 counter-reset rung for an INCP destination. "
                    "Use a condition that is mutually exclusive with the increment event."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "expected_revision",
                        "rung_id",
                        "destination_address",
                        "condition",
                        "description",
                    ],
                    "properties": {
                        "expected_revision": {"type": "integer", "minimum": 0},
                        "rung_id": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]*$",
                            "maxLength": 80,
                        },
                        "destination_address": {
                            "type": "string",
                            "pattern": "^D\\d{5}$",
                        },
                        "condition": ladder_condition_schema,
                        "description": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 500,
                        },
                    },
                    "$defs": ladder_schema_definitions,
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.sync_control_network_out_rung",
                description=(
                    "Create or repair the sole OUT rung for one retained control network. "
                    "The Host copies network, mode, step, and safety permit conditions exactly."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["expected_revision", "rung_id", "network_id"],
                    "properties": {
                        "expected_revision": {"type": "integer", "minimum": 0},
                        "rung_id": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]*$",
                            "maxLength": 80,
                        },
                        "network_id": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]*$",
                            "maxLength": 80,
                        },
                    },
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.sync_alarm_trigger_rung",
                description=(
                    "Create or repair one alarm-latch SET rung from the retained alarm's "
                    "declared trigger_all contract. The Host copies the exact reviewed "
                    "conditions and preserves unrelated ladder work."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["expected_revision", "rung_id", "alarm_id"],
                    "properties": {
                        "expected_revision": {"type": "integer", "minimum": 0},
                        "rung_id": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]*$",
                            "maxLength": 80,
                        },
                        "alarm_id": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]*$",
                            "maxLength": 80,
                        },
                    },
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.sync_alarm_recovery_rung",
                description=(
                    "Create or repair one alarm-latch RST rung from the retained recovery "
                    "rule's declared when_all contract. The Host validates that the recovery "
                    "owns the alarm before copying its exact reviewed conditions."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["expected_revision", "rung_id", "recovery_id"],
                    "properties": {
                        "expected_revision": {"type": "integer", "minimum": 0},
                        "rung_id": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]*$",
                            "maxLength": 80,
                        },
                        "recovery_id": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]*$",
                            "maxLength": 80,
                        },
                    },
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.move_ladder_rung",
                description=(
                    "Move one existing ladder rung immediately before another existing rung "
                    "without rewriting either rung. Use this for PLC scan-order diagnostics."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["expected_revision", "rung_id", "before_rung_id"],
                    "properties": {
                        "expected_revision": {"type": "integer", "minimum": 0},
                        "rung_id": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]*$",
                            "maxLength": 80,
                        },
                        "before_rung_id": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]*$",
                            "maxLength": 80,
                        },
                    },
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.repair_index_mode_latches",
                description=(
                    "Repair only the current index-acceptance mode-retention diagnostic. "
                    "The Host uses the retained role bindings, mode declarations and exact "
                    "automatic/manual OUT rungs to apply one revision-guarded atomic repair."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["expected_revision"],
                    "properties": {
                        "expected_revision": {"type": "integer", "minimum": 0}
                    },
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.repair_automatic_start_rearm",
                description=(
                    "Repair only the current index-acceptance automatic-start rearm "
                    "diagnostic. The Host gates the retained running start branch with a "
                    "stable internal release arm and places its OUT writer after running."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["expected_revision"],
                    "properties": {
                        "expected_revision": {"type": "integer", "minimum": 0}
                    },
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.repair_index_sequence_stopped_gates",
                description=(
                    "Repair only the current index-acceptance active-sequence deadlock. "
                    "The Host corrects a diagnosed stopped-state mode/sequence declaration "
                    "and removes only its matching ON contact from exact automatic/manual "
                    "sequence SET rungs while preserving every other term."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["expected_revision"],
                    "properties": {
                        "expected_revision": {"type": "integer", "minimum": 0}
                    },
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.repair_index_motion_run_gate",
                description=(
                    "Repair only the current index-acceptance motion/run-state diagnostic. "
                    "The Host gates each exact diagnosed physical-motion OUT rung with the "
                    "retained running signal while preserving all existing step and safety terms."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["expected_revision"],
                    "properties": {
                        "expected_revision": {"type": "integer", "minimum": 0}
                    },
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.repair_index_timeout_reset_gate",
                description=(
                    "Repair only the current index-acceptance timeout reset scan-order "
                    "diagnostic. The Host removes the stale timeout-done OFF contact from "
                    "the exact reviewed recovery and its latch/sequence reset rungs so one "
                    "reset pulse clears the timeout state atomically."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["expected_revision"],
                    "properties": {
                        "expected_revision": {"type": "integer", "minimum": 0}
                    },
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.repair_index_manual_stop_reset",
                description=(
                    "Repair only the current manual stop/reset resume diagnostic. "
                    "The Host adds a retained manual run permit cleared by stop/reset, "
                    "gates manual-step motion with it, and makes operator reset clear "
                    "both automatic and manual sequence groups without requiring an alarm."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["expected_revision"],
                    "properties": {
                        "expected_revision": {"type": "integer", "minimum": 0}
                    },
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.repair_stale_step_references",
                description=(
                    "Repair only current checked ladder references to undeclared sequence "
                    "steps. The Host uses exact diagnostic paths, removes a duplicate stale "
                    "SET rung only when another retained writer exists, and prunes only the "
                    "stale branch from other conditions."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["expected_revision"],
                    "properties": {
                        "expected_revision": {"type": "integer", "minimum": 0}
                    },
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.repair_control_network_contracts",
                description=(
                    "Repair only current checked control-network/OUT consistency findings. "
                    "The Host creates missing empty-permit networks only for retained internal "
                    "calculations and merges declared permits into the sole existing OUT rung."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["expected_revision"],
                    "properties": {
                        "expected_revision": {"type": "integer", "minimum": 0}
                    },
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.repair_sequence_start_release_contracts",
                description=(
                    "Repair only the current checked sequence start/release contract. "
                    "The Host maps retained automatic/manual sequences to their reviewed "
                    "momentary start bindings and atomically adds start-ON/reset-OFF terms."
                ),
                parameters_json_schema={
                    "type": "object", "additionalProperties": False,
                    "required": ["expected_revision"],
                    "properties": {
                        "expected_revision": {"type": "integer", "minimum": 0}
                    },
                },
            ),
            *collection_tools,
            AgentToolDefinitionV1(
                tool_name="design.check",
                description=(
                    "Validate the current draft without changing it. Returns exact paths, "
                    "received bad references and related declared IDs so the Agent can fix "
                    "the retained revision instead of regenerating the whole project."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["expected_revision"],
                    "properties": {"expected_revision": {"type": "integer", "minimum": 0}},
                },
            ),
            AgentToolDefinitionV1(
                tool_name="design.publish_review",
                description=(
                    "Publish only a fully checked draft as the source for the existing immutable "
                    "human-review plan. Returns the exact published draft and digest; it never "
                    "creates or modifies an XG5000 project."
                ),
                parameters_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["expected_revision"],
                    "properties": {"expected_revision": {"type": "integer", "minimum": 0}},
                },
            ),
        ]

    def _ensure(
        self,
        *,
        conversation_id: str,
        run_id: str,
        brief_digest: str,
        brief: ProjectBriefV1,
    ) -> ProjectDesignDraft:
        return self._repository.ensure(
            conversation_id=conversation_id,
            brief_digest=brief_digest,
            project_name=brief.project_name,
            run_id=run_id,
        )

    @staticmethod
    def _bounded_ladder_repair_context(
        design: Mapping[str, Any], diagnostic: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """Project a bounded executable repair target for ladder-owned findings."""

        code = str(diagnostic.get("code") or "")
        if code not in _LADDER_REPAIR_CODES:
            return None
        rungs = design.get("ladder_rungs") or []
        selected: list[Any] = []
        path = str(diagnostic.get("path") or "").lstrip("/")
        public, _, remainder = path.partition("/")
        if public == "ladderRungs":
            index_text = remainder.split("/", 1)[0]
            if index_text.isdigit() and int(index_text) < len(rungs):
                selected.append(rungs[int(index_text)])
        related = {str(value) for value in diagnostic.get("relatedIds") or []}
        for rung in rungs:
            if (
                isinstance(rung, Mapping)
                and str(rung.get("rung_id") or "") in related
                and rung not in selected
            ):
                selected.append(rung)
        instruction = (
            "Patch both retained automatic/manual mode OUT rungs in one atomic batch: each "
            "needs a stopped-only selector start branch, a self-hold retained while running "
            "but cancelled by the opposite selector while stopped, and the opposite mode OFF "
            "on every energizing path."
            if code == "index_acceptance_mode_selection_not_retained_until_stopped"
            else (
                "In one atomic batch, declare an internal release-arm bit, patch the automatic "
                "running start path to require it, add its start-OFF release writer, and consume "
                "the arm after running evaluation so a held start cannot restart the machine."
                if code == "index_acceptance_automatic_start_rearm_missing"
                else (
                    "Correct only the diagnosed stopped-state mode/sequence declaration and "
                    "matching active automatic/manual SET rung gates; preserve all other interlocks."
                    if code == "index_acceptance_active_sequence_blocked_by_stopped_signal"
                    else "Patch the retained ladder items identified by this diagnostic."
                )
            )
        )
        return {
            "section": "ladder_rungs",
            "items": _json_clone(selected[:_MAX_SECTION_CHANGES]),
            "instruction": instruction,
            "requiredDeclarations": (
                [{"kind": "internal_bit", "purpose": "automatic_start_release_arm"}]
                if code == "index_acceptance_automatic_start_rearm_missing"
                else []
            ),
        }

    @staticmethod
    def _summary(draft: ProjectDesignDraft) -> dict[str, Any]:
        document = draft.document
        design = document["native_design"]
        diagnostic_sections: set[str] = set()
        public_sections = {
            "ioPoints": "io_points",
            "operatingModes": "operating_modes",
            "controlNetworks": "control_networks",
            "ladderRungs": "ladder_rungs",
            "timers": "timers",
            "sequences": "sequences",
            "alarms": "alarms",
            "recoveryRules": "recovery_rules",
            "safetyRules": "safety_rules",
        }
        ladder_owned_codes = _LADDER_REPAIR_CODES
        code_owned_sections = {
            "alarm_output_missing_latch_off_interlock": "safety_rules",
        }
        for diagnostic in draft.diagnostics:
            code = str(diagnostic.get("code") or "")
            reference_owner = _reference_repair_section(diagnostic)
            if reference_owner is not None:
                diagnostic_sections.add(reference_owner)
                continue
            if (
                code == "native_check_failed"
                and "timer" in str(diagnostic.get("message") or "").casefold()
            ):
                diagnostic_sections.add("timers")
                continue
            if code in ladder_owned_codes:
                diagnostic_sections.add("ladder_rungs")
                continue
            if code in code_owned_sections:
                diagnostic_sections.add(code_owned_sections[code])
                continue
            if code == "control_network_out_cardinality_mismatch":
                received = diagnostic.get("received")
                network_ids = received.get("networkIds") if isinstance(received, dict) else None
                out_rung_ids = received.get("outRungIds") if isinstance(received, dict) else None
                if isinstance(network_ids, list) and len(network_ids) == 1 and not out_rung_ids:
                    diagnostic_sections.add("ladder_rungs")
                else:
                    diagnostic_sections.add("control_networks")
                continue
            path = str(diagnostic.get("path") or "").lstrip("/")
            if path.startswith("native_design/"):
                section = path.removeprefix("native_design/").split("/", 1)[0]
                if section in _COLLECTIONS:
                    diagnostic_sections.add(section)
            else:
                public = path.split("/", 1)[0]
                if public in public_sections:
                    diagnostic_sections.add(public_sections[public])
        complete_sections = []
        if document.get("narrative"):
            complete_sections.append("narrative")
        if document.get("technical_configuration") is not None:
            complete_sections.append("technical_configuration")
        complete_sections.extend(
            name for name in _COLLECTIONS if design.get(name) and name not in diagnostic_sections
        )
        repair_context: dict[str, Any] | None = None
        if draft.diagnostics:
            first = draft.diagnostics[0]
            code = str(first.get("code") or "")
            path = str(first.get("path") or "").lstrip("/")
            section = None
            remainder = ""
            reference_owner = _reference_repair_section(first)
            if reference_owner is not None:
                section = reference_owner
                # The path identifies the use site; repair belongs to the
                # missing declaration collection and therefore has no retained
                # item index to constrain.
                remainder = ""
            elif code in ladder_owned_codes:
                section = "ladder_rungs"
            elif code in code_owned_sections:
                section = code_owned_sections[code]
                # The diagnostic path can point at the source object (for
                # example an alarm) while the missing repair belongs to a
                # different collection (for example safety_rules). Do not
                # reuse the source collection's numeric index in the owner.
                remainder = ""
            elif code == "control_network_out_cardinality_mismatch":
                received = first.get("received")
                network_ids = received.get("networkIds") if isinstance(received, dict) else None
                out_rung_ids = received.get("outRungIds") if isinstance(received, dict) else None
                section = (
                    "ladder_rungs"
                    if isinstance(network_ids, list) and len(network_ids) == 1 and not out_rung_ids
                    else "control_networks"
                )
            elif path.startswith("native_design/"):
                section, _, remainder = path.removeprefix("native_design/").partition("/")
            else:
                public, _, remainder = path.partition("/")
                section = public_sections.get(public)
            if section in _COLLECTIONS:
                values = design.get(section) or []
                selected: list[Any] = []
                index_text = remainder.split("/", 1)[0]
                if index_text.isdigit() and int(index_text) < len(values):
                    selected.append(values[int(index_text)])
                related = (
                    set()
                    if reference_owner is not None
                    else {str(value) for value in first.get("relatedIds") or []}
                )
                identity_field = _IDENTITY_FIELDS[section]
                for item in values:
                    if (
                        isinstance(item, dict)
                        and str(item.get(identity_field) or "") in related
                        and item not in selected
                    ):
                        selected.append(item)
                missing_item_repair = (
                    code == "alarm_output_missing_latch_off_interlock"
                    or reference_owner is not None
                    or code
                    in {
                        "sequence_start_rung_missing_or_incomplete",
                        "sequence_transition_rung_missing_or_incomplete",
                        "sequence_reset_rung_missing_or_incomplete",
                        "recovery_sequence_reset_rung_missing_or_incomplete",
                        "increment_reset_writer_mismatch",
                        "control_network_out_cardinality_mismatch",
                    }
                )
                if not selected and not missing_item_repair:
                    selected = list(values[:_MAX_SECTION_CHANGES])
                if code == "alarm_output_missing_latch_off_interlock":
                    instruction = (
                        "Add the new safety-rule item named by received.suggestedRuleId; preserve every "
                        "existing safety rule because each alarm latch requires an independent permit."
                    )
                elif reference_owner is not None:
                    instruction = (
                        "Declare up to four missing typed references listed in missingDeclarations "
                        "in one batch. Preserve every retained item."
                        + (
                            " Allocate addresses not listed in occupiedAddresses."
                            if reference_owner == "io_points"
                            else ""
                        )
                    )
                else:
                    instruction = (
                        "Patch or remove these retained item IDs; do not create a parallel replacement "
                        "unless the diagnostic explicitly requires a missing item."
                    )
                repair_context = {
                    "section": section,
                    "items": _json_clone(selected[:_MAX_SECTION_CHANGES]),
                    "instruction": instruction,
                }
                if reference_owner is not None:
                    missing_declarations: list[dict[str, str]] = []
                    seen_missing: set[str] = set()
                    for diagnostic in draft.diagnostics:
                        if _reference_repair_section(diagnostic) != reference_owner:
                            continue
                        missing_id = str(diagnostic.get("received") or "")
                        if not missing_id or missing_id in seen_missing:
                            continue
                        seen_missing.add(missing_id)
                        missing_declarations.append(
                            {
                                "id": missing_id,
                                "expectedType": str(diagnostic.get("expectedType") or ""),
                                "usePath": str(diagnostic.get("path") or ""),
                            }
                        )
                        if len(missing_declarations) >= _MAX_SECTION_CHANGES:
                            break
                    repair_context["missingDeclarations"] = missing_declarations
                    if reference_owner == "io_points":
                        repair_context["occupiedAddresses"] = [
                            str(item.get("address") or "")
                            for item in design.get("io_points") or []
                            if isinstance(item, dict) and item.get("address")
                        ]
        visible_diagnostics = draft.diagnostics[:_MAX_SECTION_CHANGES]
        diagnostic_code_counts: dict[str, int] = {}
        diagnostic_representatives: list[dict[str, Any]] = []
        for diagnostic in draft.diagnostics:
            code = str(diagnostic.get("code") or "")
            diagnostic_code_counts[code] = diagnostic_code_counts.get(code, 0) + 1
            if (
                code
                and not any(item.get("code") == code for item in diagnostic_representatives)
                and len(diagnostic_representatives) < _MAX_SECTION_CHANGES
            ):
                diagnostic_representatives.append(diagnostic)
        repair_contexts = [
            context
            for diagnostic in visible_diagnostics
            if (context := ProjectDesignDraftService._bounded_ladder_repair_context(
                design, diagnostic
            )) is not None
        ]
        if repair_context is not None and not any(
            context.get("section") == repair_context.get("section")
            and context.get("items") == repair_context.get("items")
            for context in repair_contexts
        ):
            repair_contexts.insert(0, repair_context)
        result = {
            "status": draft.status,
            "revision": draft.revision,
            "briefDigest": draft.brief_digest,
            "draftDigest": hashlib.sha256(_json_dump(document).encode("utf-8")).hexdigest(),
            "publishedDigest": draft.published_digest,
            "completeSections": complete_sections,
            "collectionCounts": {name: len(design.get(name) or []) for name in _COLLECTIONS},
            "collectionItemIds": {
                name: [
                    str(item.get(_IDENTITY_FIELDS[name]))
                    for item in design.get(name) or []
                    if isinstance(item, dict) and item.get(_IDENTITY_FIELDS[name])
                ]
                for name in _COLLECTIONS
            },
            "remainingWork": list(document.get("remaining_work") or []),
            # Match the atomic edit bound: independent findings from one checked
            # revision can be repaired together without exposing an unbounded list.
            "diagnostics": visible_diagnostics,
            # Detailed diagnostics stay at the atomic edit bound. These two
            # projections retain all Host-defined code classes/counts and one
            # bounded representative per leading class, so repeated reference
            # errors cannot hide a later deterministic repair route.
            "diagnosticCodes": sorted(diagnostic_code_counts),
            "diagnosticCodeCounts": {
                code: diagnostic_code_counts[code]
                for code in sorted(diagnostic_code_counts)
            },
            "diagnosticRepresentatives": _json_clone(diagnostic_representatives),
            "diagnosticCount": len(draft.diagnostics),
            "remainingDiagnosticCount": max(0, len(draft.diagnostics) - len(visible_diagnostics)),
        }
        scoped_repair_tools = {
            "index_acceptance_mode_selection_not_retained_until_stopped": (
                "design.repair_index_mode_latches"
            ),
            "mode_activation_start_missing_stopped_condition": (
                "design.repair_index_mode_latches"
            ),
            "index_acceptance_automatic_start_rearm_missing": (
                "design.repair_automatic_start_rearm"
            ),
            "index_acceptance_active_sequence_blocked_by_stopped_signal": (
                "design.repair_index_sequence_stopped_gates"
            ),
            "index_acceptance_motion_not_gated_by_running": (
                "design.repair_index_motion_run_gate"
            ),
            "index_acceptance_timeout_reset_blocked_by_done_mirror": (
                "design.repair_index_timeout_reset_gate"
            ),
            "index_acceptance_manual_stop_reset_can_resume_motion": (
                "design.repair_index_manual_stop_reset"
            ),
            "control_network_out_cardinality_mismatch": (
                "design.repair_control_network_contracts"
            ),
            "control_network_out_missing_effective_permits": (
                "design.repair_control_network_contracts"
            ),
            "sequence_reset_release_missing": (
                "design.repair_sequence_start_release_contracts"
            ),
        }
        recommended_repair_tools = list(dict.fromkeys(
            scoped_repair_tools[code]
            for code in sorted(diagnostic_code_counts)
            if code in scoped_repair_tools
        ))
        if recommended_repair_tools:
            result["recommendedRepairTools"] = recommended_repair_tools
        if repair_context is not None:
            result["repairContext"] = repair_context
        if repair_contexts:
            result["repairContexts"] = repair_contexts
        return result

    @staticmethod
    def _normalize_collection_value(
        section: str,
        item_id: str,
        value: Mapping[str, Any],
        *,
        internal_simulation: bool = False,
        plc_series: str | None = None,
    ) -> dict[str, Any]:
        """Expand compact agent edits into the authoritative stored contract.

        Display-only labels and values mechanically implied by typed content
        must not become retry traps for a slow local model. Semantic choices
        such as a writable output's safe state remain mandatory.
        """

        normalized = _json_clone(dict(value))
        identity_field = _IDENTITY_FIELDS[section]
        # The edit envelope identifies the target. JSON Schema cannot express
        # equality between item_id and the nested identity field, so treating a
        # harmless naming mismatch as a model-retry condition wastes minutes
        # and can discard an otherwise valid batch. Canonicalize to the target.
        normalized[identity_field] = item_id

        def normalize_bit_address(raw: Any) -> Any:
            if not isinstance(raw, str):
                return raw
            candidate = raw.strip().upper()
            candidate = candidate.removeprefix("%")
            if candidate.startswith(("MX", "PX")):
                candidate = candidate[0] + candidate[2:]
            legacy = re.fullmatch(r"([XY])([0-9A-F]{3,4})", candidate)
            if internal_simulation and legacy is not None:
                area, offset_text = legacy.groups()
                offset = int(offset_text, 16)
                word = (0 if area == "X" else 200) + offset // 16
                bit = offset % 16
                return f"M{word:04d}{bit:X}"
            physical = re.fullmatch(r"P(\d{3,4})([0-9A-F])", candidate)
            if internal_simulation and physical is not None:
                word_text, bit_text = physical.groups()
                absolute_bit = int(word_text) * 16 + int(bit_text, 16)
                # Keep operator-driven inputs and program-written values in
                # separate M word ranges so byte stimulus writes cannot alias.
                offset = absolute_bit % 1_600
                base_word = 0 if normalized.get("kind") == "digital_input" else 200
                word = base_word + offset // 16
                bit = offset % 16
                return f"M{word:04d}{bit:X}"
            dotted = re.fullmatch(r"([PM])(\d{1,4})\.([0-9A-F])", candidate)
            if dotted is not None:
                area, word, bit = dotted.groups()
                return f"{area}{word.zfill(4)}{bit}"
            return candidate

        if section == "io_points":
            signal_id = str(normalized.get("signal_id") or item_id)
            normalized.setdefault("signal_id", signal_id)
            normalized.setdefault("symbol", signal_id.upper())
            normalized.setdefault("description", signal_id)
            address = normalized.get("address")
            normalized["address"] = normalize_bit_address(address)
            if normalized.get("kind") == "digital_input":
                normalized["safe_state"] = "not_applicable"
        elif section == "operating_modes":
            normalized.setdefault("name", item_id)
            normalized.setdefault("description", item_id)
        elif section == "control_networks":
            output_id = str(normalized.get("output_signal_id") or item_id)
            normalized.setdefault("title", f"Control network for {output_id}")
        elif section == "ladder_rungs":
            action = normalized.get("action")
            if isinstance(action, dict):
                instruction = str(action.get("instruction") or "rung")
                target = action.get("target_id") or action.get("destination_address") or item_id
                normalized.setdefault("description", f"{instruction} {target}")
        elif section == "timers":
            normalized.setdefault("name", item_id)
            normalized.setdefault("instruction", "TON")
            normalized.setdefault("description", item_id)
            if "duration_ms" not in normalized:
                time_base = normalized.get("time_base_ms")
                preset = normalized.get("preset_ticks")
                if type(time_base) is int and type(preset) is int:
                    normalized["duration_ms"] = time_base * preset
            address = normalized.get("address")
            if isinstance(address, str):
                address = address.strip().upper()
                match = re.fullmatch(r"T(\d+)", address)
                if match is not None and plc_series in {"XGK", "XGB"}:
                    width = 4 if plc_series == "XGK" else 3
                    address = f"T{int(match.group(1)):0{width}d}"
                normalized["address"] = address
        elif section == "sequences":
            normalized.setdefault("name", item_id)
            steps = normalized.get("steps")
            if isinstance(steps, list):
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    step_id = step.get("step_id")
                    if isinstance(step_id, str):
                        step.setdefault("name", step_id)
                        step.setdefault("description", step_id)
                    address = step.get("address")
                    if isinstance(address, str):
                        step["address"] = address.strip().upper()
        elif section in {"alarms", "recovery_rules"}:
            normalized.setdefault("name", item_id)
            normalized.setdefault("description", item_id)
        elif section == "safety_rules":
            normalized.setdefault("name", item_id)
            if "classification" not in normalized and "kind" in normalized:
                normalized["classification"] = normalized.pop("kind")
            classification = normalized.get("classification")
            if classification == "process_interlock":
                normalized.setdefault("action", "deenergize")
            elif classification == "external_safety_boundary":
                normalized.setdefault("action", "external_hardware")
                normalized.setdefault("permit_when", None)
        return normalized

    @staticmethod
    def _apply_changes(
        document: dict[str, Any],
        changes: Sequence[Mapping[str, Any]],
        *,
        internal_simulation: bool = False,
    ) -> dict[str, Any]:
        result = _json_clone(document)
        design = result["native_design"]
        for change in changes:
            operation = change.get("operation")
            section = change.get("section")
            if (
                operation
                not in {
                    "replace_section",
                    "create_item",
                    "patch_item",
                    "upsert_item",
                    "remove_item",
                    "move_item",
                }
                or section not in _EDITABLE_SECTIONS
            ):
                raise ProjectDesignDraftConflict("design edit operation or section is invalid")
            if operation == "replace_section":
                if "value" not in change:
                    raise ProjectDesignDraftConflict("replace_section requires value")
                if section == "narrative":
                    if not isinstance(change["value"], dict):
                        raise ProjectDesignDraftConflict("narrative must be an object")
                    result["narrative"] = _json_clone(change["value"])
                elif section == "technical_configuration":
                    result["technical_configuration"] = _json_clone(change["value"])
                elif section == "index_acceptance_bindings":
                    design[section] = ProjectPlanIndexAcceptanceBindingsV1.model_validate(
                        change["value"]
                    ).model_dump(mode="json")
                else:
                    if not isinstance(change["value"], list):
                        raise ProjectDesignDraftConflict("collection replacement must be an array")
                    design[section] = _json_clone(change["value"])
                continue
            if section not in _COLLECTIONS:
                raise ProjectDesignDraftConflict("item edits require a collection section")
            item_id = change.get("item_id")
            if not isinstance(item_id, str) or re.fullmatch(r"[a-z][a-z0-9_]*", item_id) is None:
                raise ProjectDesignDraftConflict("item edit requires a valid item_id")
            identity_field = _IDENTITY_FIELDS[section]
            values = design[section]
            position = next(
                (
                    index
                    for index, item in enumerate(values)
                    if isinstance(item, dict) and item.get(identity_field) == item_id
                ),
                None,
            )
            if operation == "remove_item":
                if position is None:
                    raise ProjectDesignDraftConflict("remove_item target does not exist")
                values.pop(position)
            elif operation == "move_item":
                if section != "ladder_rungs" or position is None:
                    raise ProjectDesignDraftConflict("move_item target does not exist")
                before_item_id = change.get("before_item_id")
                if (
                    not isinstance(before_item_id, str)
                    or before_item_id == item_id
                    or re.fullmatch(r"[a-z][a-z0-9_]*", before_item_id) is None
                ):
                    raise ProjectDesignDraftConflict(
                        "move_item requires a different valid before_item_id"
                    )
                moved = values.pop(position)
                before_position = next(
                    (
                        index
                        for index, item in enumerate(values)
                        if isinstance(item, dict)
                        and item.get(identity_field) == before_item_id
                    ),
                    None,
                )
                if before_position is None:
                    raise ProjectDesignDraftConflict("move_item anchor does not exist")
                values.insert(before_position, moved)
            else:
                value = change.get("value")
                if not isinstance(value, dict):
                    raise ProjectDesignDraftConflict(
                        f"item value must carry matching {identity_field}"
                    )
                if operation == "create_item" and position is not None:
                    raise ProjectDesignDraftConflict("create_item target already exists")
                if operation == "patch_item" and position is None:
                    raise ProjectDesignDraftConflict("patch_item target does not exist")
                if position is not None:
                    # Merge before normalization so a targeted edit cannot
                    # silently erase required engineering decisions.
                    value = {**_json_clone(values[position]), **_json_clone(value)}
                value = ProjectDesignDraftService._normalize_collection_value(
                    section,
                    item_id,
                    value,
                    internal_simulation=internal_simulation,
                    plc_series=str(
                        (result.get("technical_configuration") or {}).get("plc_series") or ""
                    ).upper()
                    or None,
                )
                if value.get(identity_field) != item_id:
                    raise ProjectDesignDraftConflict(
                        f"item value must carry matching {identity_field}"
                    )
                if position is None:
                    values.append(_json_clone(value))
                else:
                    values[position] = _json_clone(value)
        result["remaining_work"] = ["full_check"]
        return result

    @staticmethod
    def _check(
        document: dict[str, Any],
        brief: ProjectBriefV1,
        requirement_messages: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        diagnostics = _narrative_diagnostics(document)
        narrative = document.get("narrative")
        resolved_sources: dict[str, str] = {}
        if isinstance(narrative, dict):
            try:
                sources = [
                    ProjectRequirementSourceV1.model_validate(item)
                    for item in narrative.get("requirement_sources", [])
                ]
                resolved_sources = validate_requirement_sources(
                    brief,
                    sources,
                    require_complete=False,
                    requirement_messages=requirement_messages,
                )
            except (TypeError, ValueError, ValidationError) as exc:
                diagnostics.append(
                    _diagnostic(
                        "requirement_source_invalid",
                        "/narrative/requirement_sources",
                        str(exc),
                    )
                )
        technical = document.get("technical_configuration")
        if technical is None:
            diagnostics.append(
                _diagnostic(
                    "missing_section",
                    "/technical_configuration",
                    "Technical configuration is required.",
                )
            )
        else:
            try:
                ProjectPlanTechnicalConfigurationV1.model_validate(technical)
            except ValidationError as exc:
                diagnostics.extend(_validation_diagnostics(exc))
        native_design = _json_clone(document.get("native_design"))
        if not isinstance(native_design, dict):
            diagnostics.append(
                _diagnostic("schema_invalid", "/native_design", "Native design must be an object.")
            )
            return diagnostics[:_MAX_DIAGNOSTICS], None
        native_design["collection_scope"] = _bound_scope(
            brief, native_design, resolved_sources
        )
        try:
            validated_design = ProjectPlanNativeDesignV1.model_validate(native_design)
        except ValidationError as exc:
            diagnostics.extend(_validation_diagnostics(exc))
            return diagnostics[:_MAX_DIAGNOSTICS], None
        canonical_design = validated_design.model_dump(mode="json", by_alias=True)
        diagnostics.extend(_index_acceptance_binding_diagnostics(canonical_design))
        if not diagnostics:
            diagnostics.extend(_index_acceptance_behavior_diagnostics(canonical_design))
        if diagnostics:
            return diagnostics[:_MAX_DIAGNOSTICS], None
        if _brief_is_internal_simulation(brief):
            for index, point in enumerate(canonical_design["ioPoints"]):
                if str(point.get("address") or "").startswith("P"):
                    diagnostics.append(
                        _diagnostic(
                            "physical_io_forbidden_internal_simulation",
                            f"/native_design/io_points/{index}/address",
                            "This project explicitly requires internal simulated M I/O; a physical P address is not allowed.",
                            received=point.get("address"),
                            expected_type="internal M bit address",
                            related_ids=[str(point.get("signalId") or "")],
                        )
                    )
                signal_id = str(point.get("signalId") or "")
                if point.get("kind") == "digital_output" and signal_id.casefold().startswith(
                    "alarm_"
                ):
                    diagnostics.append(
                        _diagnostic(
                            "internal_simulation_alarm_indicator_requires_internal_bit",
                            f"/ioPoints/{index}/kind",
                            (
                                "A PC-only alarm indicator is an observable internal M status bit, not a "
                                "wired digital output. Change kind to internal_bit and keep safe_state off; "
                                "this avoids an artificial hardware safety-boundary requirement while "
                                "remaining visible in XG-SIM."
                            ),
                            received=point.get("kind"),
                            expected_type="internal_bit",
                            related_ids=[signal_id],
                        )
                    )
        # Confirmed user semantics take precedence over structural convergence.
        # Otherwise a repair agent can mirror a structurally valid but unsafe
        # control-network condition into ladder before the semantic check runs.
        requirement_diagnostics = _requirement_diagnostics(canonical_design, brief)
        diagnostics.extend(requirement_diagnostics)
        for finding in blocked_recovery_conditions(canonical_design):
            diagnostics.append(_diagnostic(
                "recovery_settled_conditions_contradict",
                f"/recoveryRules/{finding['index']}/whenAll",
                "Recovery cannot satisfy its settled OUT dependencies: the listed signals "
                "are required both ON and OFF. Read the recovery and dependency rungs. "
                "Repair the incorrect derived-state condition and its control-network declaration, "
                "or the incorrect recovery condition, according to the confirmed behavior. "
                "Do not remove cause-clear/reset protections merely to pass this check. "
                "This is a settled Boolean contradiction, not a simulator result.",
                received=finding,
                related_ids=[finding["recoveryId"], *finding["dependencyRungIds"]],
            ))
        if diagnostics:
            # Do not mix downstream reference/compiler consequences into the
            # same repair turn as a semantic defect.  For example, a bad mode
            # network's own modeId makes the reference checker demand a self
            # contact; presenting both instructions causes a repair oscillation.
            return diagnostics[:_MAX_DIAGNOSTICS], None
        diagnostics.extend(_reference_diagnostics(canonical_design))
        if diagnostics:
            return diagnostics[:_MAX_DIAGNOSTICS], None
        if not canonical_design["ladderRungs"]:
            return [
                _diagnostic(
                    "missing_section",
                    "/native_design/ladder_rungs",
                    "At least one ladder rung is required.",
                )
            ], None
        assert technical is not None
        validated_technical = ProjectPlanTechnicalConfigurationV1.model_validate(technical)
        artifact = {
            "technicalConfiguration": validated_technical.model_dump(mode="json", by_alias=True),
            "design": canonical_design,
        }
        try:
            _compile_native_program(
                artifact,
                plc_series=validated_technical.plc_series,
                program_name=validated_technical.program_name,
            )
        except NativeProjectPreconditionError as exc:
            message = str(exc)
            if exc.repair_section in _COLLECTIONS:
                return [_diagnostic(
                    exc.diagnostic_code, f"/native_design/{exc.repair_section}",
                    message, related_ids=list(exc.repair_item_ids),
                )], None
            folded = message.casefold()
            if "every digital output requires one control network" in folded:
                digital_outputs = {
                    str(point["signalId"])
                    for point in canonical_design["ioPoints"]
                    if point.get("kind") == "digital_output"
                }
                networked_outputs = {
                    str(network["outputSignalId"])
                    for network in canonical_design["controlNetworks"]
                }
                missing_outputs = sorted(digital_outputs - networked_outputs)
                return [
                    _diagnostic(
                        "digital_output_missing_control_network",
                        "/controlNetworks",
                        (
                            "Add one control network for each listed digital output. "
                            "For alarm indicators, use the corresponding alarm latch/trigger signal ON "
                            "as permitAll and retain the output as a separate human-readable indicator."
                        ),
                        received=missing_outputs,
                        expected_type="one control network per missing digital output",
                        related_ids=missing_outputs,
                    )
                ], None
            if "every digital output requires an explicit safety boundary" in folded:
                digital_outputs = {
                    str(point["signalId"])
                    for point in canonical_design["ioPoints"]
                    if point.get("kind") == "digital_output"
                }
                bounded_outputs = {
                    str(output_id)
                    for rule in canonical_design["safetyRules"]
                    for output_id in rule.get("affectedOutputSignalIds") or []
                }
                missing_outputs = sorted(digital_outputs - bounded_outputs)
                return [
                    _diagnostic(
                        "digital_output_missing_safety_boundary",
                        "/safetyRules",
                        (
                            "Add an explicit safety rule for each listed digital output. For an alarm "
                            "indicator, use deenergize with the same independent permit that drives its "
                            "control network (timeout: alarm_latch ON; emergency: emergency_stop ON; "
                            "door: door_open ON), so the indicator has a defined OFF safe state without "
                            "suppressing the active alarm."
                        ),
                        received=missing_outputs,
                        expected_type="one explicit safety boundary per missing digital output",
                        related_ids=missing_outputs,
                    )
                ], None
            if "must end with an explicit inverse start condition" in folded:
                message += (
                    ". Keep start_all unchanged; add the inverse of one start_all "
                    "condition to the final step complete_all"
                )
            elif "sequence reset must include an inverse start condition" in folded:
                message += (
                    ". Keep start_all unchanged; add the inverse of one start_all "
                    "condition to reset_all"
                )
            elif "start contains contradictory signal conditions" in folded:
                message += (
                    ". Remove the duplicated signal with opposite states from start_all; "
                    "the release condition belongs in the final step and reset_all"
                )
            folded = message.casefold()
            section = None
            if "every digital output requires one control network" in folded:
                section = "control_networks"
            elif "every digital output requires an explicit safety boundary" in folded:
                section = "safety_rules"
            elif "every sequence requires an explicit reset" in folded:
                section = "recovery_rules"
                message += (
                    ". Add the sequence_id to one existing recovery rule's reset_sequence_ids"
                )
            elif "timer" in folded:
                section = "timers"
            elif "sequence" in folded or "s group" in folded:
                section = "sequences"
            related_ids = []
            if section is not None:
                identity_field = _IDENTITY_FIELDS[section]
                related_ids = [
                    str(item.get(identity_field))
                    for item in native_design.get(section) or []
                    if isinstance(item, dict) and item.get(identity_field)
                ]
            diagnostic_code = (
                "sequence_reset_release_missing"
                if "sequence reset must include an inverse start condition" in folded
                else "native_check_failed"
            )
            return [
                _diagnostic(
                    diagnostic_code,
                    f"/native_design/{section}" if section else "/native_design",
                    message,
                    related_ids=related_ids,
                )
            ], None
        published = _json_clone(document)
        published["technical_configuration"] = validated_technical.model_dump(mode="json")
        published["native_design"] = validated_design.model_dump(mode="json")
        published["remaining_work"] = []
        return [], published

    async def execute(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        conversation_id: str,
        run_id: str,
        brief_digest: str,
        project_brief: Mapping[str, Any],
    ) -> dict[str, Any]:
        brief = ProjectBriefV1.model_validate(
            {
                key: value
                for key, value in project_brief.items()
                if key != "requirement_message_sources"
            }
        )
        internal_simulation = _brief_is_internal_simulation(brief)
        draft = self._ensure(
            conversation_id=conversation_id,
            run_id=run_id,
            brief_digest=brief_digest,
            brief=brief,
        )
        if tool_name == "design.inspect":
            return {
                **self._summary(draft),
                "revisionAvailability": self._repository.revision_summary(
                    conversation_id
                ),
                "revisionReadInstruction": (
                    "Use design.read without revision for the current draft, with an integer "
                    "for an immutable same-conversation revision, or with latest_published "
                    "for the newest published base. When a Host observation supplies a "
                    "reviewedPlanDigest, use published_plan_digest to read that exact base. "
                    "Historical reads never change the draft."
                ),
                "executionObservations": _prioritized_execution_observations(draft),
            }
        if tool_name == "design.read":
            sections = arguments.get("sections")
            if not isinstance(sections, list) or any(
                section not in _EDITABLE_SECTIONS for section in sections
            ):
                raise ProjectDesignDraftConflict("design.read sections are invalid")
            revision_selector = arguments.get("revision")
            published_plan_digest = arguments.get("published_plan_digest")
            if revision_selector is not None and published_plan_digest is not None:
                raise ProjectDesignDraftConflict(
                    "design.read revision and published_plan_digest are mutually exclusive"
                )
            source = draft
            if published_plan_digest is not None:
                if (
                    not isinstance(published_plan_digest, str)
                    or re.fullmatch(r"[0-9a-f]{64}", published_plan_digest) is None
                ):
                    raise ProjectDesignDraftConflict(
                        "design.read published_plan_digest must be a lowercase SHA-256 digest"
                    )
                source = self._repository.published_revision_for_digest(
                    conversation_id, published_plan_digest
                )
                missing_description = (
                    f"a published revision for plan digest {published_plan_digest}"
                )
            elif revision_selector is not None:
                if type(revision_selector) is int and revision_selector >= 0:
                    source = self._repository.get_revision(
                        conversation_id, revision_selector
                    )
                    missing_description = f"revision {revision_selector}"
                elif revision_selector == "latest_published":
                    source = self._repository.latest_published_revision(conversation_id)
                    missing_description = "a published revision"
                else:
                    raise ProjectDesignDraftConflict(
                        "design.read revision must be a non-negative integer or latest_published"
                    )
            if source is None:
                return {
                    **self._summary(draft),
                    "status": "revision_not_found",
                    "currentRevision": draft.revision,
                    "retainedRevision": draft.revision,
                    "message": (
                        f"This planning conversation does not contain {missing_description}; "
                        "the current draft was not changed."
                    ),
                }
            values: dict[str, Any] = {}
            for section in sections:
                if section == "narrative":
                    values[section] = source.document["narrative"]
                elif section == "technical_configuration":
                    values[section] = source.document["technical_configuration"]
                elif section == "index_acceptance_bindings":
                    values[section] = source.document["native_design"].get(section)
                else:
                    values[section] = source.document["native_design"][section]
            return {
                **self._summary(draft),
                "currentRevision": draft.revision,
                "readRevision": source.revision,
                "readStatus": source.status,
                "readBriefDigest": source.brief_digest,
                "readPublishedDigest": source.published_digest,
                "isCurrentRevision": source.revision == draft.revision,
                "sections": _json_clone(values),
                "executionObservations": (
                    _prioritized_execution_observations(draft)
                    if draft.diagnostics or draft.status not in {"valid", "published"}
                    else _design_execution_observations(source.document["native_design"])
                ),
            }
        expected_revision = arguments.get("expected_revision")
        if type(expected_revision) is not int or expected_revision != draft.revision:
            return {
                "status": "revision_conflict",
                "expectedRevision": expected_revision,
                "currentRevision": draft.revision,
                "message": "Inspect the current draft and rebase the intended edit.",
            }
        effective_tool_name = tool_name
        effective_arguments: Mapping[str, Any] = arguments

        scoped_index_repairs = {
            "design.repair_index_mode_latches": (
                frozenset({
                    "index_acceptance_mode_selection_not_retained_until_stopped",
                    "mode_activation_start_missing_stopped_condition",
                }),
                _index_mode_latch_repair_changes,
            ),
            "design.repair_automatic_start_rearm": (
                frozenset({"index_acceptance_automatic_start_rearm_missing"}),
                _index_start_rearm_repair_changes,
            ),
            "design.repair_index_sequence_stopped_gates": (
                frozenset({
                    "index_acceptance_active_sequence_blocked_by_stopped_signal"
                }),
                _index_sequence_stopped_gate_repair_changes,
            ),
            "design.repair_index_motion_run_gate": (
                frozenset({"index_acceptance_motion_not_gated_by_running"}),
                _index_motion_run_gate_repair_changes,
            ),
            "design.repair_index_timeout_reset_gate": (
                frozenset({
                    "index_acceptance_timeout_reset_blocked_by_done_mirror"
                }),
                _index_timeout_reset_gate_repair_changes,
            ),
            "design.repair_index_manual_stop_reset": (
                frozenset({
                    "index_acceptance_manual_stop_reset_can_resume_motion",
                    "control_network_out_cardinality_mismatch",
                    "sequence_reset_release_missing",
                }),
                _index_manual_stop_reset_repair_changes,
            ),
            "design.repair_stale_step_references": (
                frozenset({"unknown_reference"}),
                _stale_step_reference_repair_changes,
            ),
            "design.repair_control_network_contracts": (
                frozenset({
                    "control_network_out_cardinality_mismatch",
                    "control_network_out_missing_effective_permits",
                }),
                _control_network_contract_repair_changes,
            ),
            "design.repair_sequence_start_release_contracts": (
                frozenset({"sequence_reset_release_missing"}),
                _sequence_start_release_contract_repair_changes,
            ),
        }
        if tool_name in scoped_index_repairs:
            required_codes, build_changes = scoped_index_repairs[tool_name]
            current_diagnostic = next(
                (
                    item for item in draft.diagnostics
                    if str(item.get("code") or "") in required_codes
                ),
                None,
            )
            if current_diagnostic is None:
                return {
                    **self._summary(draft),
                    "status": "not_applied",
                    "currentRevision": draft.revision,
                    "retainedRevision": draft.revision,
                    "diagnostics": [_diagnostic(
                        "repair_diagnostic_not_current",
                        "/expected_revision",
                        "This scoped repair is available only for its current checked diagnostic.",
                        expected_type=" or ".join(sorted(required_codes)),
                    )],
                    "nextAction": "design.check",
                }
            try:
                candidate = self._apply_changes(
                    draft.document,
                    (
                        build_changes(
                            draft.document["native_design"], draft.diagnostics
                        )
                        if tool_name in {
                            "design.repair_index_mode_latches",
                            "design.repair_index_sequence_stopped_gates",
                            "design.repair_index_motion_run_gate",
                            "design.repair_index_timeout_reset_gate",
                            "design.repair_index_manual_stop_reset",
                            "design.repair_stale_step_references",
                            "design.repair_control_network_contracts",
                            "design.repair_sequence_start_release_contracts",
                        }
                        else build_changes(draft.document["native_design"])
                    ),
                    internal_simulation=internal_simulation,
                )
                if tool_name == "design.repair_automatic_start_rearm":
                    rungs = candidate["native_design"]["ladder_rungs"]
                    running_id = str(
                        candidate["native_design"]["index_acceptance_bindings"]
                        ["signals"]["running"]
                    )
                    running_index = next(
                        index for index, rung in enumerate(rungs)
                        if isinstance(rung, Mapping)
                        and isinstance(rung.get("action"), Mapping)
                        and rung["action"].get("instruction") == "OUT"
                        and rung["action"].get("target_id") == running_id
                    )
                    arm_index = next(
                        index for index, rung in enumerate(rungs)
                        if isinstance(rung, Mapping)
                        and rung.get("rung_id") == "automatic_start_release_arm_out"
                    )
                    arm_rung = rungs.pop(arm_index)
                    if arm_index < running_index:
                        running_index -= 1
                    rungs.insert(running_index + 1, arm_rung)
                partial = _json_clone(candidate["native_design"])
                partial["collection_scope"] = None
                validated_candidate = ProjectPlanNativeDesignV1.model_validate(partial)
                remaining_behavior = _index_acceptance_behavior_diagnostics(
                    validated_candidate.model_dump(mode="json", by_alias=True)
                )
                if any(item.get("code") in required_codes for item in remaining_behavior):
                    raise ProjectDesignDraftConflict(
                        "the retained design cannot satisfy this scoped repair"
                    )
                requirement_messages = {
                    item["message_id"]: item["content"]
                    for item in project_brief.get("requirement_message_sources", [])
                    if isinstance(item, Mapping)
                    and isinstance(item.get("message_id"), str)
                    and isinstance(item.get("content"), str)
                }
                diagnostics, checked = self._check(
                    candidate, brief, requirement_messages
                )
                if any(item.get("code") in required_codes for item in diagnostics):
                    raise ProjectDesignDraftConflict(
                        "the retained design cannot satisfy this scoped repair"
                    )
            except (
                ProjectDesignDraftConflict,
                StopIteration,
                ValidationError,
                TypeError,
                ValueError,
            ) as exc:
                return {
                    **self._summary(draft),
                    "status": "not_applied",
                    "currentRevision": draft.revision,
                    "retainedRevision": draft.revision,
                    "diagnostics": [
                        current_diagnostic,
                        _diagnostic(
                            "index_acceptance_scoped_repair_not_applicable",
                            str(current_diagnostic.get("path") or ""),
                            str(exc),
                            expected_type=" or ".join(sorted(required_codes)),
                            related_ids=list(current_diagnostic.get("relatedIds") or []),
                        ),
                    ],
                }
            stored_document = checked if checked is not None else candidate
            validation_status = "valid" if not diagnostics else "invalid"
            updated = self._repository.update(
                conversation_id=conversation_id,
                expected_revision=draft.revision,
                brief_digest=brief_digest,
                document=stored_document,
                diagnostics=diagnostics,
                status=validation_status,
                published_digest=None,
                run_id=run_id,
            )
            return {
                **self._summary(updated),
                "status": "applied",
                "validationStatus": validation_status,
                "repairedDiagnostics": sorted(required_codes),
            }

        def contract_condition(items: Any) -> dict[str, Any]:
            contacts = [
                {
                    "kind": "contact",
                    "source_type": "signal",
                    "source_id": str(item.get("signal_id") or ""),
                    "state": item.get("state"),
                }
                for item in (items or [])
                if isinstance(item, dict)
            ]
            if len(contacts) == 1:
                return contacts[0]
            return {"kind": "all_of", "terms": contacts}

        def find_sequence(sequence_id: str) -> dict[str, Any]:
            sequence = next(
                (
                    item
                    for item in draft.document["native_design"]["sequences"]
                    if isinstance(item, dict)
                    and str(item.get("sequence_id") or "") == sequence_id
                ),
                None,
            )
            if sequence is None:
                raise ProjectDesignDraftConflict("declared sequence was not found")
            return sequence

        def mode_activation_condition(sequence: Mapping[str, Any]) -> dict[str, Any] | None:
            mode_id = str(sequence.get("mode_id") or "")
            if not mode_id:
                return None
            mode = next(
                (
                    item
                    for item in draft.document["native_design"]["operating_modes"]
                    if isinstance(item, dict) and str(item.get("mode_id") or "") == mode_id
                ),
                None,
            )
            if mode is None or not isinstance(mode.get("activation"), dict):
                raise ProjectDesignDraftConflict("sequence operating mode was not found")
            activation = mode["activation"]
            return {
                "kind": "contact",
                "source_type": "signal",
                "source_id": activation.get("signal_id"),
                "state": activation.get("state"),
            }

        def compose_condition(terms: list[dict[str, Any]]) -> dict[str, Any]:
            if len(terms) == 1:
                return terms[0]
            return {"kind": "all_of", "terms": terms}

        def contract_terms(items: Any) -> list[dict[str, Any]]:
            condition = contract_condition(items)
            if condition.get("kind") == "all_of":
                return list(condition.get("terms") or [])
            return [condition]

        def sequence_rung_edit(
            *, rung_id: str, condition: dict[str, Any], description: str, action: dict[str, Any]
        ) -> Mapping[str, Any]:
            rung_exists = any(
                isinstance(item, dict) and str(item.get("rung_id") or "") == rung_id
                for item in draft.document["native_design"]["ladder_rungs"]
            )
            value = {
                "condition": condition,
                "description": description,
                "action": action,
            }
            if not rung_exists:
                value = {"rung_id": rung_id, **value}
            return {
                "expected_revision": expected_revision,
                "changes": [
                    {
                        "operation": "patch_item" if rung_exists else "create_item",
                        "item_id": rung_id,
                        "value": value,
                    }
                ],
            }

        if tool_name == "design.sync_sequence_start_rung":
            sequence_id = str(arguments.get("sequence_id") or "")
            sequence = find_sequence(sequence_id)
            steps = sequence.get("steps") or []
            if not steps or not isinstance(steps[0], dict):
                raise ProjectDesignDraftConflict("sequence has no first step")
            terms = [
                {
                    "kind": "contact",
                    "source_type": "sequence_reset",
                    "source_id": sequence_id,
                    "state": "on",
                },
                *contract_terms(sequence.get("start_all")),
            ]
            mode_condition = mode_activation_condition(sequence)
            if mode_condition is not None:
                terms.append(mode_condition)
            effective_tool_name = "design.edit_ladder_rungs"
            effective_arguments = sequence_rung_edit(
                rung_id=str(arguments.get("rung_id") or ""),
                condition=compose_condition(terms),
                description=f"{sequence_id} 시작 조건 동기화",
                action={
                    "instruction": "SET",
                    "target_type": "step",
                    "target_id": steps[0].get("step_id"),
                },
            )
        elif tool_name == "design.sync_sequence_transition_rung":
            sequence_id = str(arguments.get("sequence_id") or "")
            source_step_id = str(arguments.get("source_step_id") or "")
            sequence = find_sequence(sequence_id)
            steps = sequence.get("steps") or []
            step_index = next(
                (
                    index
                    for index, item in enumerate(steps)
                    if isinstance(item, dict)
                    and str(item.get("step_id") or "") == source_step_id
                ),
                None,
            )
            if step_index is None:
                raise ProjectDesignDraftConflict("declared source step was not found")
            step = steps[step_index]
            terms = [
                {
                    "kind": "contact",
                    "source_type": "step",
                    "source_id": source_step_id,
                    "state": "on",
                },
                *contract_terms(step.get("complete_all")),
            ]
            mode_condition = mode_activation_condition(sequence)
            if mode_condition is not None:
                terms.append(mode_condition)
            if step_index + 1 < len(steps):
                target_type = "step"
                target_id = steps[step_index + 1].get("step_id")
            else:
                target_type = "sequence_reset"
                target_id = sequence_id
            effective_tool_name = "design.edit_ladder_rungs"
            effective_arguments = sequence_rung_edit(
                rung_id=str(arguments.get("rung_id") or ""),
                condition=compose_condition(terms),
                description=f"{sequence_id} {source_step_id} 전환 조건 동기화",
                action={
                    "instruction": "SET",
                    "target_type": target_type,
                    "target_id": target_id,
                },
            )
        elif tool_name == "design.sync_sequence_reset_rung":
            sequence_id = str(arguments.get("sequence_id") or "")
            sequence = find_sequence(sequence_id)
            if not sequence.get("reset_all"):
                raise ProjectDesignDraftConflict("sequence has no explicit reset contract")
            effective_tool_name = "design.edit_ladder_rungs"
            effective_arguments = sequence_rung_edit(
                rung_id=str(arguments.get("rung_id") or ""),
                condition=contract_condition(sequence.get("reset_all")),
                description=f"{sequence_id} 명시적 초기화 조건 동기화",
                action={
                    "instruction": "SET",
                    "target_type": "sequence_reset",
                    "target_id": sequence_id,
                },
            )
        elif tool_name == "design.set_ladder_counter_reset_action":
            effective_tool_name = "design.edit_ladder_rungs"
            effective_arguments = sequence_rung_edit(
                rung_id=str(arguments.get("rung_id") or ""),
                condition=arguments.get("condition"),
                description=str(arguments.get("description") or ""),
                action={
                    "instruction": "MOV",
                    "source_constant": 0,
                    "destination_address": arguments.get("destination_address"),
                },
            )
        elif tool_name == "design.sync_control_network_out_rung":
            network_id = str(arguments.get("network_id") or "")
            network = next(
                (
                    item
                    for item in draft.document["native_design"]["control_networks"]
                    if isinstance(item, dict)
                    and str(item.get("network_id") or "") == network_id
                ),
                None,
            )
            if network is None:
                raise ProjectDesignDraftConflict("declared control network was not found")
            terms = contract_terms(network.get("permit_all"))
            mode_id = str(network.get("mode_id") or "")
            if mode_id:
                mode = next(
                    (
                        item
                        for item in draft.document["native_design"]["operating_modes"]
                        if isinstance(item, dict)
                        and str(item.get("mode_id") or "") == mode_id
                    ),
                    None,
                )
                if mode is None or not isinstance(mode.get("activation"), dict):
                    raise ProjectDesignDraftConflict("control-network mode was not found")
                activation = mode["activation"]
                terms.append(
                    {
                        "kind": "contact",
                        "source_type": "signal",
                        "source_id": activation.get("signal_id"),
                        "state": activation.get("state"),
                    }
                )
            step_id = str(network.get("step_id") or "")
            if step_id:
                terms.append(
                    {
                        "kind": "contact",
                        "source_type": "step",
                        "source_id": step_id,
                        "state": "on",
                    }
                )
            output_signal_id = str(network.get("output_signal_id") or "")
            for rule in draft.document["native_design"]["safety_rules"]:
                if (
                    isinstance(rule, dict)
                    and rule.get("classification") == "process_interlock"
                    and output_signal_id in (rule.get("affected_output_signal_ids") or [])
                    and isinstance(rule.get("permit_when"), dict)
                ):
                    terms.extend(contract_terms([rule["permit_when"]]))
            unique_terms: list[dict[str, Any]] = []
            seen_terms: set[tuple[str, str, str]] = set()
            for term in terms:
                key = (
                    str(term.get("source_type") or ""),
                    str(term.get("source_id") or ""),
                    str(term.get("state") or ""),
                )
                if key not in seen_terms:
                    seen_terms.add(key)
                    unique_terms.append(term)
            if not unique_terms:
                raise ProjectDesignDraftConflict(
                    "This internal network has no common permit condition. "
                    "Use design.edit_ladder_rungs to write its actual contact/OR/timer "
                    "condition; synchronizing an empty declaration cannot derive the logic."
                )
            effective_tool_name = "design.edit_ladder_rungs"
            effective_arguments = sequence_rung_edit(
                rung_id=str(arguments.get("rung_id") or ""),
                condition=compose_condition(unique_terms),
                description=f"{network.get('title') or network_id} 출력 조건 동기화",
                action={
                    "instruction": "OUT",
                    "target_type": "signal",
                    "target_id": output_signal_id,
                },
            )
        elif tool_name == "design.sync_alarm_trigger_rung":
            alarm_id = str(arguments.get("alarm_id") or "")
            alarm = next(
                (
                    item
                    for item in draft.document["native_design"]["alarms"]
                    if isinstance(item, dict) and str(item.get("alarm_id") or "") == alarm_id
                ),
                None,
            )
            if alarm is None:
                raise ProjectDesignDraftConflict("declared alarm was not found")
            rung_id = str(arguments.get("rung_id") or "")
            rung_exists = any(
                isinstance(item, dict) and str(item.get("rung_id") or "") == rung_id
                for item in draft.document["native_design"]["ladder_rungs"]
            )
            rung_value = {
                "condition": contract_condition(alarm.get("trigger_all")),
                "description": f"{alarm.get('name') or alarm_id} 원인 래치",
                "action": {
                    "instruction": "SET",
                    "target_type": "signal",
                    "target_id": alarm.get("latch_signal_id"),
                },
            }
            if not rung_exists:
                rung_value = {"rung_id": rung_id, **rung_value}
            effective_tool_name = "design.edit_ladder_rungs"
            effective_arguments = {
                "expected_revision": expected_revision,
                "changes": [
                    {
                        "operation": "patch_item" if rung_exists else "create_item",
                        "item_id": rung_id,
                        "value": rung_value,
                    }
                ],
            }
        elif tool_name == "design.sync_alarm_recovery_rung":
            recovery_id = str(arguments.get("recovery_id") or "")
            recovery = next(
                (
                    item
                    for item in draft.document["native_design"]["recovery_rules"]
                    if isinstance(item, dict)
                    and str(item.get("recovery_id") or "") == recovery_id
                ),
                None,
            )
            if recovery is None:
                raise ProjectDesignDraftConflict("declared recovery rule was not found")
            reset_alarm_ids = [str(value) for value in recovery.get("reset_alarm_ids") or []]
            if len(reset_alarm_ids) != 1:
                raise ProjectDesignDraftConflict(
                    "alarm recovery rung requires exactly one owned alarm"
                )
            alarm_id = reset_alarm_ids[0]
            alarm = next(
                (
                    item
                    for item in draft.document["native_design"]["alarms"]
                    if isinstance(item, dict) and str(item.get("alarm_id") or "") == alarm_id
                ),
                None,
            )
            if alarm is None:
                raise ProjectDesignDraftConflict("recovery alarm was not found")
            rung_id = str(arguments.get("rung_id") or "")
            rung_exists = any(
                isinstance(item, dict) and str(item.get("rung_id") or "") == rung_id
                for item in draft.document["native_design"]["ladder_rungs"]
            )
            rung_value = {
                "condition": contract_condition(recovery.get("when_all")),
                "description": f"{recovery.get('name') or recovery_id} 래치 해제",
                "action": {
                    "instruction": "RST",
                    "target_type": "signal",
                    "target_id": alarm.get("latch_signal_id"),
                },
            }
            if not rung_exists:
                rung_value = {"rung_id": rung_id, **rung_value}
            effective_tool_name = "design.edit_ladder_rungs"
            effective_arguments = {
                "expected_revision": expected_revision,
                "changes": [
                    {
                        "operation": "patch_item" if rung_exists else "create_item",
                        "item_id": rung_id,
                        "value": rung_value,
                    }
                ],
            }
        elif tool_name == "design.set_ladder_increment_action":
            rung_id = str(arguments.get("rung_id") or "")
            rung_exists = any(
                isinstance(item, dict) and str(item.get("rung_id") or "") == rung_id
                for item in draft.document["native_design"]["ladder_rungs"]
            )
            rung_value = {
                "condition": arguments.get("condition"),
                "description": arguments.get("description"),
                "action": {
                    "instruction": "INCP",
                    "destination_address": arguments.get("destination_address"),
                },
            }
            if not rung_exists:
                rung_value = {"rung_id": rung_id, **rung_value}
            effective_tool_name = "design.edit_ladder_rungs"
            effective_arguments = {
                "expected_revision": expected_revision,
                "changes": [
                    {
                        "operation": "patch_item" if rung_exists else "create_item",
                        "item_id": rung_id,
                        "value": rung_value,
                    }
                ],
            }
        elif tool_name == "design.patch_ladder_rung":
            effective_tool_name = "design.edit_ladder_rungs"
            effective_arguments = {
                "expected_revision": expected_revision,
                "changes": [
                    {
                        "operation": "patch_item",
                        "item_id": arguments.get("rung_id"),
                        "value": arguments.get("patch"),
                    }
                ],
            }
        elif tool_name == "design.move_ladder_rung":
            effective_tool_name = "design.edit_ladder_rungs"
            effective_arguments = {
                "expected_revision": expected_revision,
                "changes": [
                    {
                        "operation": "move_item",
                        "item_id": arguments.get("rung_id"),
                        "before_item_id": arguments.get("before_rung_id"),
                    }
                ],
            }
        if (
            effective_tool_name.startswith("design.edit_")
            and effective_tool_name != "design.edit_io_safe_states"
        ):
            section = effective_tool_name.removeprefix("design.edit_")
            if section not in _COLLECTIONS:
                raise ProjectDesignDraftConflict("unknown collection edit tool")
            raw_changes = effective_arguments.get("changes")
            if (
                not isinstance(raw_changes, list)
                or not raw_changes
                or len(raw_changes) > _MAX_SECTION_CHANGES
            ):
                raise ProjectDesignDraftConflict("collection edit changes are invalid")
            document = _json_clone(draft.document)
            diagnostics: list[dict[str, Any]] = []
            applied_changes = 0
            existing_item_ids = {
                str(item.get(_IDENTITY_FIELDS[section]))
                for item in document["native_design"][section]
                if isinstance(item, dict) and item.get(_IDENTITY_FIELDS[section])
            }
            diagnosed_item_ids: set[str] = set()
            existing_item_patch_batch = all(
                isinstance(change, dict)
                and change.get("operation") == "patch_item"
                and str(change.get("item_id") or "") in existing_item_ids
                for change in raw_changes
            )
            restrict_repair_to_diagnosed_items = False
            forbid_repair_removal = False
            first_diagnostic: Mapping[str, Any] = {}
            first_code = ""
            if draft.diagnostics:
                first_diagnostic = draft.diagnostics[0]
                first_code = str(first_diagnostic.get("code") or "")
                diagnosed_removal_is_valid = first_code in {
                    "unbound_sequence_rung",
                    "contradictory_safety_boundaries_for_output",
                    "safety_rule_targets_alarm_indicator",
                    "timer_ton_rung_cardinality",
                }
                first_path = str(first_diagnostic.get("path") or "").lstrip("/")
                public_sections = {
                    "ioPoints": "io_points",
                    "operatingModes": "operating_modes",
                    "controlNetworks": "control_networks",
                    "ladderRungs": "ladder_rungs",
                    "timers": "timers",
                    "sequences": "sequences",
                    "alarms": "alarms",
                    "recoveryRules": "recovery_rules",
                    "safetyRules": "safety_rules",
                }
                if first_path.startswith("native_design/"):
                    diagnosed_section, _, diagnosed_remainder = first_path.removeprefix(
                        "native_design/"
                    ).partition("/")
                else:
                    public_section, _, diagnosed_remainder = first_path.partition("/")
                    diagnosed_section = public_sections.get(public_section)
                code_owned_section = {
                    **dict.fromkeys(_LADDER_REPAIR_CODES, "ladder_rungs"),
                    "alarm_output_missing_latch_off_interlock": "safety_rules",
                }.get(first_code)
                if first_code == "control_network_out_cardinality_mismatch":
                    received = first_diagnostic.get("received")
                    network_ids = (
                        received.get("networkIds") if isinstance(received, dict) else None
                    )
                    out_rung_ids = (
                        received.get("outRungIds") if isinstance(received, dict) else None
                    )
                    if isinstance(network_ids, list) and len(network_ids) == 1 and not out_rung_ids:
                        code_owned_section = "ladder_rungs"
                reference_owner = _reference_repair_section(first_diagnostic)
                if reference_owner is not None:
                    code_owned_section = reference_owner
                if (
                    (first_code == "control_network_out_missing_effective_permits"
                     and section == "control_networks")
                    or (first_code in {
                            "sequence_start_rung_missing_or_incomplete",
                            "sequence_transition_rung_missing_or_incomplete",
                            "sequence_reset_rung_missing_or_incomplete",
                        }
                        and section == "sequences")
                ):
                    # The brief is authoritative; either the mutable network
                    # declaration or its rung may be wrong. Allow the agent to
                    # repair the identified declaration instead of forcing a
                    # correct OR rung to copy a mistaken AND contract.
                    code_owned_section = None
                if (
                    first_code == "increment_reset_writer_mismatch"
                    and isinstance(first_diagnostic.get("received"), dict)
                    and not first_diagnostic["received"].get("resetRungIds")
                ):
                    # The path identifies the increment, but the missing writer
                    # must be added (or an incorrect reset writer corrected).
                    # Do not restrict that repair to the increment's item ID.
                    code_owned_section = "ladder_rungs"
                if code_owned_section is not None:
                    diagnosed_section = code_owned_section
                    diagnosed_remainder = ""
                if (
                    diagnosed_section is not None and diagnosed_section != section
                    and not existing_item_patch_batch
                ):
                    return {
                        **self._summary(draft),
                        "status": "not_applied",
                        "currentRevision": draft.revision,
                        "retainedRevision": draft.revision,
                        "diagnostics": [
                            first_diagnostic,
                            _diagnostic(
                                "repair_section_mismatch",
                                "/changes",
                                (
                                    f"The current diagnostic targets {diagnosed_section!r}; "
                                    f"an edit to {section!r} was not applied. Repair the diagnosed "
                                    "section first and preserve unrelated validated work."
                                ),
                                received=section,
                                expected_type=diagnosed_section,
                                related_ids=list(first_diagnostic.get("relatedIds") or []),
                            ),
                        ],
                    }
                if diagnosed_section == section:
                    diagnosed_index = diagnosed_remainder.split("/", 1)[0]
                    current_items = document["native_design"][section]
                    if diagnosed_index.isdigit() and int(diagnosed_index) < len(current_items):
                        diagnosed_item = current_items[int(diagnosed_index)]
                        if isinstance(diagnosed_item, dict):
                            diagnosed_item_id = diagnosed_item.get(_IDENTITY_FIELDS[section])
                            if diagnosed_item_id:
                                diagnosed_item_ids.add(str(diagnosed_item_id))
                                restrict_repair_to_diagnosed_items = True
                                forbid_repair_removal = not diagnosed_removal_is_valid
                    missing_counter_reset = (
                        first_code == "increment_reset_writer_mismatch"
                        and isinstance(first_diagnostic.get("received"), dict)
                        and not first_diagnostic["received"].get("resetRungIds")
                    )
                    related_existing = (
                        set()
                        if reference_owner is not None or missing_counter_reset
                        or first_code == "recovery_sequence_reset_rung_missing_or_incomplete"
                        else {
                            str(item_id)
                            for item_id in first_diagnostic.get("relatedIds") or []
                            if str(item_id) in existing_item_ids
                        }
                    )
                    if (
                        related_existing
                        and first_code != "alarm_output_missing_latch_off_interlock"
                    ):
                        # Some consistency failures involve two retained items (for
                        # example an increment writer and its reset writer).  Both are
                        # legitimate repair targets even when the diagnostic path can
                        # point at only one collection index.
                        diagnosed_item_ids.update(related_existing)
                        restrict_repair_to_diagnosed_items = True
                        forbid_repair_removal = not diagnosed_removal_is_valid
                    if not diagnosed_remainder:
                        if related_existing:
                            diagnosed_item_ids.update(related_existing)
                    elif first_code == "native_check_failed":
                        # Native preflight errors point at the collection root.  When the
                        # compiler names an existing item, repair only that item.  Otherwise
                        # preserve all existing items and allow patches, but never wholesale
                        # deletion of valid siblings while chasing one compiler diagnostic.
                        diagnostic_message = str(first_diagnostic.get("message") or "")
                        mentioned = {
                            item_id
                            for item_id in existing_item_ids
                            if item_id and item_id in diagnostic_message
                        }
                        if mentioned:
                            diagnosed_item_ids = mentioned
                            restrict_repair_to_diagnosed_items = True
                        forbid_repair_removal = True
            native_repair_requires_existing_item = any(
                isinstance(item, dict)
                and str(item.get("code") or "") == "native_check_failed"
                and str(item.get("path") or "") == f"/native_design/{section}"
                for item in draft.diagnostics
            )
            for index, change in enumerate(raw_changes):
                change_item_id = (
                    str(change.get("item_id") or "") if isinstance(change, dict) else ""
                )
                if (
                    restrict_repair_to_diagnosed_items
                    and change_item_id not in diagnosed_item_ids
                    and not existing_item_patch_batch
                ):
                    diagnostics.append(
                        _diagnostic(
                            "repair_must_target_diagnosed_item",
                            f"/changes/{index}/item_id",
                            "Repair only the item identified by the current diagnostic; preserve unrelated valid items.",
                            received=change_item_id,
                            related_ids=sorted(diagnosed_item_ids),
                        )
                    )
                    continue
                if (
                    forbid_repair_removal
                    and isinstance(change, dict)
                    and change.get("operation") == "remove_item"
                ):
                    diagnostics.append(
                        _diagnostic(
                            "repair_must_patch_not_remove",
                            f"/changes/{index}/operation",
                            "Patch the diagnosed item in place; removing retained design work is not a valid repair for this diagnostic.",
                            received=change.get("operation"),
                            related_ids=(
                                sorted(diagnosed_item_ids)
                                if diagnosed_item_ids
                                else sorted(existing_item_ids)
                            ),
                        )
                    )
                    continue
                if (
                    native_repair_requires_existing_item
                    and existing_item_ids
                    and isinstance(change, dict)
                    and change.get("operation") in {"create_item", "upsert_item"}
                    and str(change.get("item_id") or "") not in existing_item_ids
                ):
                    diagnostics.append(
                        _diagnostic(
                            "repair_must_target_existing_item",
                            f"/changes/{index}/item_id",
                            "This native preflight repair must patch an existing item; do not add a parallel replacement.",
                            received=change.get("item_id"),
                            related_ids=sorted(existing_item_ids),
                        )
                    )
                    continue
                effective_change = (
                    {**change, "section": section} if isinstance(change, dict) else change
                )
                if (
                    first_code == "alarm_output_missing_latch_off_interlock"
                    and isinstance(effective_change, dict)
                    and effective_change.get("operation")
                    in {"create_item", "patch_item", "upsert_item"}
                    and isinstance(effective_change.get("value"), dict)
                    and isinstance(first_diagnostic.get("received"), dict)
                ):
                    received = first_diagnostic["received"]
                    suggested_rule_id = str(received.get("suggestedRuleId") or "")
                    if effective_change.get("item_id") == suggested_rule_id:
                        effective_change = {
                            **effective_change,
                            "value": {
                                **effective_change["value"],
                                "rule_id": suggested_rule_id,
                                "classification": "process_interlock",
                                "permit_when": {
                                    "signal_id": str(received.get("latchSignalId") or ""),
                                    "state": "off",
                                },
                                "affected_output_signal_ids": [
                                    str(received.get("outputSignalId") or "")
                                ],
                                "action": "deenergize",
                            },
                        }
                try:
                    candidate = self._apply_changes(
                        document,
                        [effective_change],
                        internal_simulation=internal_simulation,
                    )
                    partial = _json_clone(candidate["native_design"])
                    partial["collection_scope"] = None
                    ProjectPlanNativeDesignV1.model_validate(partial)
                except (
                    ProjectDesignDraftConflict,
                    ValidationError,
                    TypeError,
                    ValueError,
                ) as exc:
                    failures = (
                        _validation_diagnostics(exc)
                        if isinstance(exc, ValidationError)
                        else [_diagnostic("edit_rejected", "", str(exc))]
                    )
                    diagnostics.extend(
                        {
                            **failure,
                            "path": f"/changes/{index}{failure.get('path', '')}",
                        }
                        for failure in failures
                    )
                    continue
                document = candidate
                applied_changes += 1
            if applied_changes > 0 and all(
                document.get(field) == draft.document.get(field)
                for field in ("narrative", "technical_configuration", "native_design")
            ):
                no_change = _diagnostic(
                    "edit_made_no_change",
                    str((draft.diagnostics[0] if draft.diagnostics else {}).get("path") or ""),
                    "No editable design field changed. The proposed patch is identical to the retained item; "
                    "marking the draft for another check is not a program edit. Change the field needed "
                    "for the requested behavior instead of echoing its current value.",
                    related_ids=(
                        list(
                            (draft.diagnostics[0] if draft.diagnostics else {}).get("relatedIds")
                            or []
                        )
                    ),
                )
                return {
                    **self._summary(draft),
                    "status": "not_applied",
                    "currentRevision": draft.revision,
                    "retainedRevision": draft.revision,
                    "diagnostics": [
                        no_change,
                        *draft.diagnostics[:1],
                    ],
                    "diagnosticsFreshness": "retained_not_rechecked",
                    "nextAction": "design.check",
                }
            if tool_name == "design.move_ladder_rung" and applied_changes > 0:
                repeated_revision = self._repository.prior_run_revision_with_ladder(
                    conversation_id,
                    run_id,
                    draft.revision,
                    document["native_design"]["ladder_rungs"],
                )
                if repeated_revision is not None:
                    repeated_order = _diagnostic(
                        "ladder_move_repeats_prior_order",
                        "/changes/0",
                        "This rung move would restore a ladder order already produced by the current run. "
                        "It was not applied. Repair the current validated diagnostic instead of cycling "
                        "between scan orders.",
                        received={"priorRevision": repeated_revision},
                        expected_type="a non-cyclic rung order change justified by a current diagnostic",
                        related_ids=[
                            str(arguments.get("rung_id") or ""),
                            str(arguments.get("before_rung_id") or ""),
                        ],
                    )
                    return {
                        **self._summary(draft),
                        "status": "not_applied",
                        "currentRevision": draft.revision,
                        "retainedRevision": draft.revision,
                        "diagnostics": [repeated_order, *draft.diagnostics[:1]],
                        "diagnosticsFreshness": "retained_not_rechecked",
                        "nextAction": "design.check" if draft.diagnostics else "design.edit",
                    }
            if applied_changes == 0:
                updated = self._repository.update(
                    conversation_id=conversation_id,
                    expected_revision=draft.revision,
                    brief_digest=brief_digest,
                    document=document,
                    diagnostics=diagnostics,
                    status="working",
                    published_digest=None,
                    run_id=run_id,
                )
                return {
                    **self._summary(updated),
                    "status": "not_applied",
                    "currentRevision": updated.revision,
                    "retainedRevision": updated.revision,
                }
            updated = self._repository.update(
                conversation_id=conversation_id,
                expected_revision=draft.revision,
                brief_digest=brief_digest,
                document=document,
                diagnostics=diagnostics,
                status="working",
                published_digest=None,
                run_id=run_id,
            )
            return {
                **self._summary(updated),
                "status": "applied" if not diagnostics else "partially_applied",
                "appliedChanges": applied_changes,
                "rejectedChanges": len(raw_changes) - applied_changes,
                **({"editImpact": _ladder_edit_impact(
                    draft.document["native_design"]["ladder_rungs"],
                    updated.document["native_design"]["ladder_rungs"],
                )} if section == "ladder_rungs" else {}),
            }
        if tool_name == "design.edit_io_safe_states":
            changes = arguments.get("changes")
            if not isinstance(changes, list) or not changes or len(changes) > 512:
                raise ProjectDesignDraftConflict("I/O safe-state changes are invalid")
            document = _json_clone(draft.document)
            io_points = document["native_design"]["io_points"]
            io_by_id = {item.get("signal_id"): item for item in io_points if isinstance(item, dict)}
            try:
                for change in changes:
                    if not isinstance(change, dict) or set(change) != {
                        "signal_id",
                        "safe_state",
                    }:
                        raise ProjectDesignDraftConflict("I/O safe-state change shape is invalid")
                    signal_id = change["signal_id"]
                    item = io_by_id.get(signal_id)
                    if item is None:
                        raise ProjectDesignDraftConflict(f"unknown io_point {signal_id!r}")
                    item["safe_state"] = change["safe_state"]
                partial = _json_clone(document["native_design"])
                partial["collection_scope"] = None
                ProjectPlanNativeDesignV1.model_validate(partial)
            except (
                ProjectDesignDraftConflict,
                ValidationError,
                TypeError,
                ValueError,
            ) as exc:
                diagnostics = (
                    _validation_diagnostics(exc)
                    if isinstance(exc, ValidationError)
                    else [_diagnostic("edit_rejected", "/changes", str(exc))]
                )
                return {
                    "status": "not_applied",
                    "currentRevision": draft.revision,
                    "retainedRevision": draft.revision,
                    "diagnostics": diagnostics,
                }
            document["remaining_work"] = ["full_check"]
            updated = self._repository.update(
                conversation_id=conversation_id,
                expected_revision=draft.revision,
                brief_digest=brief_digest,
                document=document,
                diagnostics=[],
                status="working",
                published_digest=None,
                run_id=run_id,
            )
            return {**self._summary(updated), "status": "applied"}
        if effective_tool_name == "design.edit":
            changes = effective_arguments.get("changes")
            # A checked diagnostic must not turn into a global edit lock.  The
            # advertised workbench permits metadata replacements together with
            # atomic collection-item repairs, which is required when a sourced
            # follow-up changes the narrative and its implementation in one
            # revision.  Continue to reject unadvertised whole-collection
            # replacement while repairing an invalid draft.
            scoped_edit = (
                isinstance(changes, list)
                and bool(changes)
                and all(
                    isinstance(change, dict)
                    and (
                        (
                            change.get("operation") == "replace_section"
                            and change.get("section")
                            in {
                                "narrative",
                                "technical_configuration",
                                "index_acceptance_bindings",
                            }
                        )
                        or (
                            change.get("operation")
                            in {
                                "create_item",
                                "patch_item",
                                "upsert_item",
                                "remove_item",
                                "move_item",
                            }
                            and change.get("section") in _COLLECTIONS
                        )
                    )
                    for change in changes
                )
            )
            if draft.diagnostics and not scoped_edit:
                first_diagnostic = draft.diagnostics[0]
                return {
                    **self._summary(draft),
                    "status": "not_applied",
                    "currentRevision": draft.revision,
                    "retainedRevision": draft.revision,
                    "diagnostics": [
                        first_diagnostic,
                        _diagnostic(
                            "repair_requires_scoped_edit_tool",
                            "/changes",
                            (
                                "Use atomic item edits for executable collections; whole-collection "
                                "replacement is not available while repairing a diagnosed draft. "
                                "Narrative, technical configuration and acceptance bindings may be "
                                "replaced alongside related item edits."
                            ),
                            related_ids=list(first_diagnostic.get("relatedIds") or []),
                        ),
                    ],
                }
            if not isinstance(changes, list) or not changes:
                raise ProjectDesignDraftConflict("design.edit changes are invalid")
            try:
                document = self._apply_changes(
                    draft.document,
                    changes,
                    internal_simulation=internal_simulation,
                )
                # Reject only malformed section shapes here. Cross-section and
                # completeness failures remain observable through design.check.
                if document.get("technical_configuration") is not None:
                    ProjectPlanTechnicalConfigurationV1.model_validate(
                        document["technical_configuration"]
                    )
                partial = _json_clone(document["native_design"])
                partial["collection_scope"] = None
                ProjectPlanNativeDesignV1.model_validate(partial)
            except (ProjectDesignDraftConflict, ValidationError, TypeError, ValueError) as exc:
                diagnostics = (
                    _validation_diagnostics(exc)
                    if isinstance(exc, ValidationError)
                    else [_diagnostic("edit_rejected", "/changes", str(exc))]
                )
                return {
                    "status": "not_applied",
                    "currentRevision": draft.revision,
                    "retainedRevision": draft.revision,
                    "diagnostics": diagnostics,
                }
            if all(
                document.get(field) == draft.document.get(field)
                for field in ("narrative", "technical_configuration", "native_design")
            ):
                retained_diagnostic = draft.diagnostics[:1]
                no_change = _diagnostic(
                    "edit_made_no_change",
                    str((retained_diagnostic[0] if retained_diagnostic else {}).get("path") or ""),
                    (
                        "No editable design field changed. The retained diagnostics were not "
                        "recomputed by this no-op; call design.check to refresh validation "
                        "before making the next repair."
                    ),
                    related_ids=list(
                        (retained_diagnostic[0] if retained_diagnostic else {}).get("relatedIds")
                        or []
                    ),
                )
                return {
                    **self._summary(draft),
                    "status": "not_applied",
                    "currentRevision": draft.revision,
                    "retainedRevision": draft.revision,
                    "diagnostics": [no_change, *retained_diagnostic],
                    "diagnosticsFreshness": "retained_not_rechecked",
                    "nextAction": "design.check",
                }
            updated = self._repository.update(
                conversation_id=conversation_id,
                expected_revision=draft.revision,
                brief_digest=brief_digest,
                document=document,
                # Any real edit invalidates the prior check result. The append-only
                # previous revision retains that history; this revision is unvalidated.
                diagnostics=[],
                status="working",
                published_digest=None,
                run_id=run_id,
            )
            return {**self._summary(updated), "status": "applied"}
        if tool_name not in {"design.check", "design.publish_review"}:
            raise ProjectDesignDraftConflict("unknown design workspace tool")
        check_document = _json_clone(draft.document)
        if internal_simulation:
            io_points = [
                self._normalize_collection_value(
                    "io_points",
                    str(point.get("signal_id") or "signal"),
                    point,
                    internal_simulation=True,
                )
                for point in check_document["native_design"]["io_points"]
                if isinstance(point, dict)
            ]

            def bit_location(address: Any) -> tuple[int, int] | None:
                match = re.fullmatch(r"M(\d{3,4})([0-9A-F])", str(address))
                if match is None:
                    return None
                word_text, bit_text = match.groups()
                return int(word_text), int(bit_text, 16)

            input_bytes = {
                (location[0], location[1] // 8)
                for point in io_points
                if point.get("kind") == "digital_input"
                and (location := bit_location(point.get("address"))) is not None
            }
            used_addresses = {str(point.get("address")) for point in io_points}
            relocation_index = 0
            for point in io_points:
                if point.get("kind") == "digital_input":
                    continue
                location = bit_location(point.get("address"))
                if location is None or (location[0], location[1] // 8) not in input_bytes:
                    continue
                while True:
                    word = 200 + relocation_index // 16
                    bit = relocation_index % 16
                    relocation_index += 1
                    candidate = f"M{word:04d}{bit:X}"
                    if candidate not in used_addresses and (word, bit // 8) not in input_bytes:
                        used_addresses.add(candidate)
                        point["address"] = candidate
                        break
            check_document["native_design"]["io_points"] = io_points
        requirement_messages = {
            item["message_id"]: item["content"]
            for item in project_brief.get("requirement_message_sources", [])
            if isinstance(item, Mapping)
            and isinstance(item.get("message_id"), str)
            and isinstance(item.get("content"), str)
        }
        diagnostics, published = self._check(
            check_document, brief, requirement_messages
        )
        if diagnostics:
            updated = self._repository.update(
                conversation_id=conversation_id,
                expected_revision=draft.revision,
                brief_digest=brief_digest,
                document=check_document,
                diagnostics=diagnostics,
                status="invalid",
                published_digest=None,
                run_id=run_id,
            )
            return {
                "status": "invalid",
                "checkedRevision": draft.revision,
                **self._summary(updated),
            }
        assert published is not None
        if tool_name == "design.check":
            updated = self._repository.update(
                conversation_id=conversation_id,
                expected_revision=draft.revision,
                brief_digest=brief_digest,
                document=published,
                diagnostics=[],
                status="valid",
                published_digest=None,
                run_id=run_id,
            )
            return {"status": "valid", "checkedRevision": draft.revision, **self._summary(updated)}
        digest = hashlib.sha256(_json_dump(published).encode("utf-8")).hexdigest()
        updated = self._repository.update(
            conversation_id=conversation_id,
            expected_revision=draft.revision,
            brief_digest=brief_digest,
            document=published,
            diagnostics=[],
            status="published",
            published_digest=digest,
            run_id=run_id,
        )
        return {
            "status": "published",
            "publishedRevision": updated.revision,
            "publishedDigest": digest,
            "briefDigest": brief_digest,
            "draft": _json_clone(updated.document),
        }
