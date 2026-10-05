from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, TypedDict

from .agent_protocol import validated_transcript_checkpoint
from .api_models import canonical_engineering_mission_snapshot
from .change_outcome import change_outcome_evidence, change_outcome_projection
from .models import (
    ApprovalDecision,
    ChangeApproval,
    ChangeRiskLevel,
    ChangeTargetIdentity,
    ChangeTransaction,
    ChangeTransactionState,
    Conversation,
    ConversationDetail,
    EngineeringAgentCheckpoint,
    EngineeringAgentTranscript,
    EngineeringMission,
    Message,
    MessageRole,
    ProjectPlanReview,
    Run,
    RunState,
    StoredEvent,
)
from .project_planning import (
    calculate_project_brief_digest,
    calculate_project_plan_attachment_revision_digest,
    project_plan_review_item_ids,
    validate_project_plan_scope_evidence,
)


class ChangeStoreConflict(RuntimeError):
    """A durable change contract conflicted with existing server authority."""


class ApprovedChangePreflightFailure(TypedDict):
    status: Literal["failed_before_execution"]
    current: bool
    native_execution_started: Literal[False]
    mutation_attempted: Literal[False]
    reason_code: str
    stage: str
    recorded_at: str


_CHANGE_PREFLIGHT_REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]{2,79}\Z")
_CHANGE_PREFLIGHT_STAGE = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")


def _is_current_approved_preflight_failure(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("status") == "failed_before_execution"
        and value.get("current") is True
        and value.get("native_execution_started") is False
        and value.get("mutation_attempted") is False
        and isinstance(value.get("reason_code"), str)
        and _CHANGE_PREFLIGHT_REASON_CODE.fullmatch(value["reason_code"]) is not None
        and isinstance(value.get("stage"), str)
        and _CHANGE_PREFLIGHT_STAGE.fullmatch(value["stage"]) is not None
        and isinstance(value.get("recorded_at"), str)
    )


class ConversationWorkflowConflict(RuntimeError):
    """A message attempted to change a conversation's fixed workflow."""


class ConversationActiveRunConflict(RuntimeError):
    """A conversation already owns a non-terminal agent run."""


class ProjectPlanReviewConflict(RuntimeError):
    """A plan review did not match the latest reviewable server artifact."""


class EngineeringMissionConflict(RuntimeError):
    """A mission checkpoint conflicted with durable conversation authority."""


_NATIVE_DESIGN_COLLECTIONS = (
    "ioPoints",
    "operatingModes",
    "controlNetworks",
    "timers",
    "sequences",
    "alarms",
    "recoveryRules",
    "safetyRules",
)


def _native_design_is_reviewable(design: Any) -> bool:
    if not isinstance(design, dict) or design.get("schemaVersion") != "2":
        return False
    collections = {name: design.get(name) for name in _NATIVE_DESIGN_COLLECTIONS}
    if any(not isinstance(value, list) for value in collections.values()):
        return False
    ladder_rungs = design.get("ladderRungs")
    if not isinstance(ladder_rungs, list) or not ladder_rungs:
        return False
    scope = design.get("collectionScope")
    if scope is None:
        return False
    if not isinstance(scope, dict) or set(scope) != set(_NATIVE_DESIGN_COLLECTIONS):
        return False
    for name, values in collections.items():
        decision = scope.get(name)
        if not isinstance(decision, dict) or set(decision) != {
            "status",
            "reason",
            "evidence",
            "basis",
        }:
            return False
        status = decision.get("status")
        reason = decision.get("reason")
        evidence = decision.get("evidence")
        basis = decision.get("basis")
        if (
            status not in {"confirmed", "not_applicable"}
            or not isinstance(reason, str)
            or not reason.strip()
            or not isinstance(evidence, list)
            or not evidence
            or any(not isinstance(item, str) or not item.strip() for item in evidence)
            or (status == "confirmed" and basis is not None)
            or (status == "not_applicable" and not isinstance(basis, str))
        ):
            return False
        if status == "confirmed" and not values:
            return False
        if status == "not_applicable" and values:
            return False
    return all(scope[name].get("status") == "confirmed" for name in ("ioPoints", "controlNetworks"))




class MutationLeaseConflict(ChangeStoreConflict):
    """Another transaction already owns the target mutation scope."""

    def __init__(self, scope: str, transaction_id: str) -> None:
        super().__init__(f"mutation scope {scope!r} is held by {transaction_id}")
        self.scope = scope
        self.transaction_id = transaction_id


class TargetOperationLeaseConflict(ChangeStoreConflict):
    """The single Host-owned XG5000 target-operation lease is unavailable."""

    def __init__(
        self,
        *,
        operation_id: str,
        owner_kind: str,
        stale: bool,
    ) -> None:
        state = "stale and requires explicit recovery" if stale else "active"
        super().__init__(
            "XG5000 target operations are serialized; "
            f"{owner_kind} operation {operation_id} is {state}"
        )
        self.operation_id = operation_id
        self.owner_kind = owner_kind
        self.stale = stale


class ApprovalExpiredConflict(ChangeStoreConflict):
    """An approved contract expired before its mutation lease was acquired."""


_NATIVE_CHECKPOINT_ORDER = {
    "SNAPSHOT_CREATED": 1,
    "BACKUP_CREATED": 2,
    "PLANNED": 3,
}
_NATIVE_CHECKPOINT_IMMUTABLE_FIELDS = frozenset(
    {
        "host_transaction_id",
        "host_plan_digest",
        "mcp_transaction_id",
        "snapshot_id",
        "snapshot_digest",
        "artifact_visibility",
        "backup_id",
        "mcp_plan_id",
        "mcp_plan_digest",
        "session_id",
        "workspace_id",
        "project_path",
        "project_hash",
        "project_identity_digest",
        "program_id",
        "program_name",
        "plan_kind",
        "target_program_digest",
        
        "before_content_state",
        "before_empty_program_source",
        
        "adapter_binding_digest",
        "runtime_identity_digest",
        "baseline_digest",
        "rollback_ld_sha256",
        "lease_generation",
        "token_offset",
        "expected",
        "replacement",
        "native_replacement",
        "payload_canonical_sha256_planned",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _json_dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_load(value: str) -> dict[str, Any]:
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise TypeError("stored JSON value is not an object")
    return loaded


MIGRATIONS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (
        1,
        "initial_server_authority_store",
        (
            """
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY,
                title TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL
                    REFERENCES conversations(id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                role TEXT NOT NULL
                    CHECK (role IN ('user', 'assistant', 'system', 'tool')),
                content TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE (conversation_id, sequence)
            )
            """,
            """
            CREATE TABLE runs (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL
                    REFERENCES conversations(id) ON DELETE CASCADE,
                state TEXT NOT NULL,
                request_message_id TEXT
                    REFERENCES messages(id) ON DELETE SET NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                error_code TEXT,
                error_detail TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT
                    REFERENCES conversations(id) ON DELETE CASCADE,
                run_id TEXT
                    REFERENCES runs(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX idx_messages_conversation ON messages(conversation_id, sequence)",
            "CREATE INDEX idx_runs_conversation ON runs(conversation_id, created_at)",
            "CREATE INDEX idx_events_conversation ON events(conversation_id, sequence)",
            "CREATE INDEX idx_events_run ON events(run_id, sequence)",
        ),
    ),
    (
        2,
        "openai_compatible_model_settings",
        (
            """
            CREATE TABLE model_settings (
                slot TEXT PRIMARY KEY
                    CHECK (slot = 'default'),
                protocol TEXT NOT NULL
                    CHECK (protocol = 'openai_chat_completions'),
                base_url TEXT NOT NULL,
                model TEXT NOT NULL,
                secret_ref TEXT NOT NULL,
                timeout_seconds REAL NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        ),
    ),
    (
        3,
        "idempotent_run_operations",
        (
            "ALTER TABLE runs ADD COLUMN operation_id TEXT",
            """
            CREATE UNIQUE INDEX idx_runs_operation_id
            ON runs(operation_id)
            WHERE operation_id IS NOT NULL
            """,
        ),
    ),
    (
        4,
        "verified_model_capabilities",
        (
            "ALTER TABLE model_settings ADD COLUMN capability_verified_at TEXT",
        ),
    ),
    (
        5,
        "server_owned_host_state",
        (
            """
            CREATE TABLE host_state (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        ),
    ),
    (
        6,
        "durable_change_transactions",
        (
            """
            CREATE TABLE change_transactions (
                id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL UNIQUE,
                conversation_id TEXT
                    REFERENCES conversations(id) ON DELETE SET NULL,
                run_id TEXT
                    REFERENCES runs(id) ON DELETE SET NULL,
                state TEXT NOT NULL CHECK (
                    state IN (
                        'PENDING_APPROVAL', 'APPROVED', 'EXECUTING', 'VERIFYING',
                        'OBSERVING',
                        'COMPLETED', 'ROLLED_BACK', 'FAILED', 'QUARANTINED',
                        'REJECTED', 'EXPIRED'
                    )
                ),
                target_identity_json TEXT NOT NULL,
                mutation_scope TEXT NOT NULL,
                risk_level TEXT NOT NULL CHECK (
                    risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')
                ),
                intent TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                plan_digest TEXT NOT NULL,
                requested_by_actor_id TEXT NOT NULL,
                approval_expires_at TEXT NOT NULL,
                result_json TEXT NOT NULL DEFAULT '{}',
                error_code TEXT,
                error_detail TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                execution_started_at TEXT,
                verification_started_at TEXT,
                completed_at TEXT
            )
            """,
            """
            CREATE TABLE change_approvals (
                transaction_id TEXT PRIMARY KEY
                    REFERENCES change_transactions(id) ON DELETE CASCADE,
                decision TEXT NOT NULL CHECK (decision IN ('APPROVE', 'REJECT')),
                plan_digest TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                decided_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE mutation_leases (
                id TEXT PRIMARY KEY,
                mutation_scope TEXT NOT NULL,
                transaction_id TEXT NOT NULL UNIQUE
                    REFERENCES change_transactions(id) ON DELETE CASCADE,
                acquired_at TEXT NOT NULL,
                released_at TEXT,
                release_reason TEXT
            )
            """,
            """
            CREATE UNIQUE INDEX idx_mutation_leases_active_scope
            ON mutation_leases(mutation_scope)
            WHERE released_at IS NULL
            """,
            """
            CREATE INDEX idx_change_transactions_conversation
            ON change_transactions(conversation_id, created_at)
            """,
            """
            CREATE INDEX idx_change_transactions_state
            ON change_transactions(state, updated_at)
            """,
        ),
    ),
    (
        7,
        "host_target_operation_lease",
        (
            """
            CREATE TABLE target_operation_leases (
                id TEXT PRIMARY KEY,
                scope_key TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                owner_kind TEXT NOT NULL,
                owner_instance_id TEXT NOT NULL,
                holder_token TEXT NOT NULL,
                target_identity_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK (
                    state IN ('ACTIVE', 'STALE_BLOCKED', 'RELEASED')
                ),
                acquired_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                released_at TEXT,
                release_reason TEXT
            )
            """,
            """
            CREATE UNIQUE INDEX idx_target_operation_leases_active_scope
            ON target_operation_leases(scope_key)
            WHERE released_at IS NULL
            """,
            """
            CREATE INDEX idx_target_operation_leases_operation
            ON target_operation_leases(operation_id, released_at)
            """,
        ),
    ),
    (
        8,
        "conversation_workflow_authority",
        (
            """
            ALTER TABLE conversations ADD COLUMN workflow TEXT
                CHECK (
                    workflow IS NULL OR workflow IN (
                        'engineering', 'new_project_planning'
                    )
                )
            """,
            """
            ALTER TABLE conversations ADD COLUMN project_brief_json TEXT
                NOT NULL DEFAULT '{}'
            """,
            """
            UPDATE conversations
            SET workflow = COALESCE(
                (
                    SELECT CASE
                        WHEN json_extract(messages.metadata_json, '$.workflow')
                            = 'new_project_planning'
                        THEN 'new_project_planning'
                        ELSE 'engineering'
                    END
                    FROM messages
                    WHERE messages.conversation_id = conversations.id
                      AND messages.role = 'user'
                    ORDER BY messages.sequence ASC
                    LIMIT 1
                ),
                'engineering'
            )
            WHERE workflow IS NULL
            """,
        ),
    ),
    (
        9,
        "engineering_conversation_target_binding",
        (
            "ALTER TABLE conversations ADD COLUMN workspace_id TEXT",
            "ALTER TABLE conversations ADD COLUMN revision_id TEXT",
            "ALTER TABLE conversations ADD COLUMN engineering_session_id TEXT",
            """
            CREATE INDEX idx_conversations_engineering_target
            ON conversations(
                workflow, workspace_id, revision_id, engineering_session_id,
                updated_at
            )
            """,
        ),
    ),
    (
        10,
        "durable_change_receipt_acknowledgement",
        (
            "ALTER TABLE change_transactions ADD COLUMN receipt_acknowledged_at TEXT",
            "ALTER TABLE change_transactions ADD COLUMN receipt_acknowledged_by TEXT",
        ),
    ),
    (
        11,
        "digest_bound_project_plan_reviews",
        (
            """
            CREATE TABLE project_plan_reviews (
                message_id TEXT NOT NULL
                    REFERENCES messages(id) ON DELETE CASCADE,
                plan_digest TEXT NOT NULL,
                conversation_id TEXT NOT NULL
                    REFERENCES conversations(id) ON DELETE CASCADE,
                actor_id TEXT NOT NULL,
                reviewed_at TEXT NOT NULL,
                PRIMARY KEY(message_id, plan_digest)
            )
            """,
            """
            CREATE INDEX idx_project_plan_reviews_conversation
            ON project_plan_reviews(conversation_id, reviewed_at)
            """,
        ),
    ),
    (
        12,
        "project_plan_review_section_scope",
        (
            """
            ALTER TABLE project_plan_reviews
            ADD COLUMN reviewed_section_ids_json TEXT NOT NULL DEFAULT '[]'
            """,
        ),
    ),
    (
        13,
        "inline_model_credentials",
        (
            "ALTER TABLE model_settings RENAME TO model_settings_credential_legacy",
            """
            CREATE TABLE model_settings (
                slot TEXT PRIMARY KEY CHECK (slot = 'default'),
                protocol TEXT NOT NULL CHECK (protocol = 'openai_chat_completions'),
                base_url TEXT NOT NULL,
                model TEXT NOT NULL,
                secret_payload_json TEXT NOT NULL,
                timeout_seconds REAL NOT NULL,
                capability_verified_at TEXT,
                updated_at TEXT NOT NULL
            )
            """,
            """
            INSERT INTO model_settings(
                slot, protocol, base_url, model, secret_payload_json,
                timeout_seconds, capability_verified_at, updated_at
            )
            SELECT
                slot, protocol, base_url, model,
                '{"schema_version":1,"api_key":null,"custom_headers":{}}',
                timeout_seconds, capability_verified_at, updated_at
            FROM model_settings_credential_legacy
            """,
            "DROP TABLE model_settings_credential_legacy",
        ),
    ),
    (
        14,
        "exact_project_plan_review_authority",
        (
            "ALTER TABLE project_plan_reviews ADD COLUMN schema_version TEXT NOT NULL DEFAULT '1'",
            "ALTER TABLE project_plan_reviews ADD COLUMN reviewed_item_ids_json TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE project_plan_reviews ADD COLUMN project_brief_digest TEXT",
            "ALTER TABLE project_plan_reviews ADD COLUMN attachment_revision_digest TEXT",
        ),
    ),
    (
        15,
        "accepted_native_project_conversation_handoff",
        (
            "ALTER TABLE conversations ADD COLUMN handoff_native_project_id TEXT",
            "ALTER TABLE conversations ADD COLUMN lineage_planning_conversation_id TEXT",
            "ALTER TABLE conversations ADD COLUMN engineering_handoff_json TEXT NOT NULL DEFAULT '{}'",
            """
            CREATE UNIQUE INDEX idx_conversations_handoff_native_project
            ON conversations(handoff_native_project_id)
            WHERE handoff_native_project_id IS NOT NULL
            """,
            """
            CREATE UNIQUE INDEX idx_conversations_lineage_planning
            ON conversations(lineage_planning_conversation_id)
            WHERE lineage_planning_conversation_id IS NOT NULL
            """,
        ),
    ),
    (
        16,
        "durable_engineering_mission_checkpoint",
        (
            """
            CREATE TABLE engineering_missions (
                conversation_id TEXT PRIMARY KEY
                    REFERENCES conversations(id) ON DELETE CASCADE,
                workspace_id TEXT NOT NULL,
                revision_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK (revision >= 1),
                snapshot_json TEXT NOT NULL,
                digest TEXT NOT NULL,
                updated_by_run_id TEXT NOT NULL
                    REFERENCES runs(id) ON DELETE RESTRICT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX idx_engineering_missions_target
            ON engineering_missions(workspace_id, revision_id, updated_at)
            """,
        ),
    ),
    (
        17,
        "durable_engineering_agent_checkpoint",
        (
            """
            CREATE TABLE engineering_agent_checkpoints (
                conversation_id TEXT PRIMARY KEY
                    REFERENCES conversations(id) ON DELETE CASCADE,
                workspace_id TEXT NOT NULL,
                revision_id TEXT NOT NULL,
                mission_digest TEXT NOT NULL,
                cycle INTEGER NOT NULL CHECK (cycle BETWEEN 1 AND 3),
                checkpoint_json TEXT NOT NULL,
                digest TEXT NOT NULL,
                updated_by_run_id TEXT NOT NULL
                    REFERENCES runs(id) ON DELETE RESTRICT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX idx_engineering_agent_checkpoints_target
            ON engineering_agent_checkpoints(workspace_id, revision_id, updated_at)
            """,
        ),
    ),
    (
        18,
        "expand_engineering_agent_autonomous_cycle_budget",
        (
            "ALTER TABLE engineering_agent_checkpoints RENAME TO engineering_agent_checkpoints_cycle3",
            """
            CREATE TABLE engineering_agent_checkpoints (
                conversation_id TEXT PRIMARY KEY
                    REFERENCES conversations(id) ON DELETE CASCADE,
                workspace_id TEXT NOT NULL,
                revision_id TEXT NOT NULL,
                mission_digest TEXT NOT NULL,
                cycle INTEGER NOT NULL CHECK (cycle BETWEEN 1 AND 8),
                checkpoint_json TEXT NOT NULL,
                digest TEXT NOT NULL,
                updated_by_run_id TEXT NOT NULL
                    REFERENCES runs(id) ON DELETE RESTRICT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            INSERT INTO engineering_agent_checkpoints(
                conversation_id, workspace_id, revision_id, mission_digest,
                cycle, checkpoint_json, digest, updated_by_run_id,
                created_at, updated_at
            )
            SELECT
                conversation_id, workspace_id, revision_id, mission_digest,
                cycle, checkpoint_json, digest, updated_by_run_id,
                created_at, updated_at
            FROM engineering_agent_checkpoints_cycle3
            """,
            "DROP TABLE engineering_agent_checkpoints_cycle3",
            """
            CREATE INDEX idx_engineering_agent_checkpoints_target
            ON engineering_agent_checkpoints(workspace_id, revision_id, updated_at)
            """,
        ),
    ),
    (
        20,
        "bind_engineering_mission_change_transaction",
        (
            """
            ALTER TABLE engineering_missions
            ADD COLUMN change_transaction_id TEXT
                REFERENCES change_transactions(id) ON DELETE RESTRICT
            """,
            """
            UPDATE engineering_missions
            SET change_transaction_id = (
                SELECT MIN(candidate.id)
                FROM change_transactions AS candidate
                WHERE candidate.conversation_id = engineering_missions.conversation_id
                  AND candidate.run_id = engineering_missions.updated_by_run_id
            )
            WHERE json_extract(snapshot_json, '$.status') = 'waiting_for_approval'
              AND (
                  SELECT COUNT(*)
                  FROM change_transactions AS candidate
                  WHERE candidate.conversation_id = engineering_missions.conversation_id
                    AND candidate.run_id = engineering_missions.updated_by_run_id
              ) = 1
            """,
            """
            CREATE INDEX idx_engineering_missions_change_transaction
            ON engineering_missions(change_transaction_id)
            """,
        ),
    ),
    (
        21,
        "recover_legacy_engineering_mission_change_binding",
        (
            """
            UPDATE engineering_missions
            SET change_transaction_id = (
                SELECT MIN(candidate.id)
                FROM change_transactions AS candidate
                WHERE candidate.conversation_id = engineering_missions.conversation_id
                  AND json_extract(candidate.target_identity_json, '$.workspace_id')
                      = engineering_missions.workspace_id
                  AND json_extract(candidate.target_identity_json, '$.revision_id')
                      = engineering_missions.revision_id
                  AND (
                      (
                          candidate.state = 'PENDING_APPROVAL'
                          AND julianday(candidate.approval_expires_at) > julianday('now')
                      )
                      OR candidate.state IN (
                          'APPROVED', 'EXECUTING', 'VERIFYING',
                          'OBSERVING', 'QUARANTINED'
                      )
                  )
            )
            WHERE change_transaction_id IS NULL
              AND json_extract(snapshot_json, '$.status') = 'waiting_for_approval'
              AND (
                  SELECT COUNT(*)
                  FROM change_transactions AS candidate
                  WHERE candidate.conversation_id = engineering_missions.conversation_id
                    AND json_extract(candidate.target_identity_json, '$.workspace_id')
                        = engineering_missions.workspace_id
                    AND json_extract(candidate.target_identity_json, '$.revision_id')
                        = engineering_missions.revision_id
                    AND (
                        (
                            candidate.state = 'PENDING_APPROVAL'
                            AND julianday(candidate.approval_expires_at) > julianday('now')
                        )
                        OR candidate.state IN (
                            'APPROVED', 'EXECUTING', 'VERIFYING',
                            'OBSERVING', 'QUARANTINED'
                        )
                    )
              ) = 1
            """,
            """
            UPDATE engineering_missions
            SET change_transaction_id = (
                SELECT MIN(candidate.id)
                FROM change_transactions AS candidate
                WHERE candidate.conversation_id = engineering_missions.conversation_id
                  AND json_extract(candidate.target_identity_json, '$.workspace_id')
                      = engineering_missions.workspace_id
                  AND json_extract(candidate.target_identity_json, '$.revision_id')
                      = engineering_missions.revision_id
            )
            WHERE change_transaction_id IS NULL
              AND json_extract(snapshot_json, '$.status') = 'waiting_for_approval'
              AND (
                  SELECT COUNT(*)
                  FROM change_transactions AS candidate
                  WHERE candidate.conversation_id = engineering_missions.conversation_id
                    AND json_extract(candidate.target_identity_json, '$.workspace_id')
                        = engineering_missions.workspace_id
                    AND json_extract(candidate.target_identity_json, '$.revision_id')
                        = engineering_missions.revision_id
              ) = 1
            """,
        ),
    ),
    (
        22,
        "allow_planning_revision_after_engineering_handoff",
        (
            "DROP INDEX IF EXISTS idx_conversations_lineage_planning",
            """
            CREATE INDEX idx_conversations_lineage_planning
            ON conversations(lineage_planning_conversation_id, created_at)
            WHERE lineage_planning_conversation_id IS NOT NULL
            """,
        ),
    ),
    (
        23,
        "persist_provider_model_context_observation",
        (
            "ALTER TABLE model_settings ADD COLUMN model_context_json TEXT",
        ),
    ),
    (
        24,
        "persist_model_reasoning_effort",
        (
            """
            ALTER TABLE model_settings ADD COLUMN reasoning_effort TEXT CHECK (
                reasoning_effort IS NULL OR reasoning_effort IN (
                    'none', 'minimal', 'low', 'medium',
                    'high', 'xhigh', 'max', 'ultra'
                )
            )
            """,
        ),
    ),
    (
        25,
        "separate_approved_artifact_and_execution_session",
        ("ALTER TABLE change_transactions ADD COLUMN execution_target_json TEXT",),
    ),
    (
        26,
        "protected_model_credential_migration_quarantine",
        (
            """
            CREATE TABLE model_credential_quarantine (
                reference TEXT PRIMARY KEY,
                protected_payload BLOB NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
        ),
    ),
    (
        27,
        "durable_completed_agent_transcripts",
        (
            """CREATE TABLE engineering_agent_transcripts (
                source_run_id TEXT PRIMARY KEY REFERENCES runs(id),
                conversation_id TEXT NOT NULL REFERENCES conversations(id),
                workspace_id TEXT,
                revision_id TEXT,
                request_message_id TEXT NOT NULL REFERENCES messages(id),
                cursor INTEGER NOT NULL,
                checkpoint_json TEXT NOT NULL,
                digest TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            "CREATE INDEX engineering_agent_transcripts_context ON engineering_agent_transcripts(conversation_id, workspace_id, revision_id, updated_at)",
        ),
    ),
    (
        28,
        "remove_engineering_checkpoint_cycle_limit",
        (
            "ALTER TABLE engineering_agent_checkpoints RENAME TO engineering_agent_checkpoints_bounded",
            """
            CREATE TABLE engineering_agent_checkpoints (
                conversation_id TEXT PRIMARY KEY
                    REFERENCES conversations(id) ON DELETE CASCADE,
                workspace_id TEXT NOT NULL,
                revision_id TEXT NOT NULL,
                mission_digest TEXT NOT NULL,
                cycle INTEGER NOT NULL CHECK (cycle >= 1),
                checkpoint_json TEXT NOT NULL,
                digest TEXT NOT NULL,
                updated_by_run_id TEXT NOT NULL
                    REFERENCES runs(id) ON DELETE RESTRICT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            INSERT INTO engineering_agent_checkpoints(
                conversation_id, workspace_id, revision_id, mission_digest,
                cycle, checkpoint_json, digest, updated_by_run_id,
                created_at, updated_at
            )
            SELECT
                conversation_id, workspace_id, revision_id, mission_digest,
                cycle, checkpoint_json, digest, updated_by_run_id,
                created_at, updated_at
            FROM engineering_agent_checkpoints_bounded
            """,
            "DROP TABLE engineering_agent_checkpoints_bounded",
            """
            CREATE INDEX idx_engineering_agent_checkpoints_target
            ON engineering_agent_checkpoints(workspace_id, revision_id, updated_at)
            """,
        ),
    ),
)

_CHANGE_TRANSACTION_OBSERVING_SCHEMA_VERSION = 19
HOST_SCHEMA_VERSION = max(
    MIGRATIONS[-1][0], _CHANGE_TRANSACTION_OBSERVING_SCHEMA_VERSION
)


def _rebuild_change_transactions_for_observing(
    connection: sqlite3.Connection,
) -> None:
    """Add OBSERVING to the SQLite CHECK constraint without losing child FKs."""

    # SQLite cannot alter a CHECK constraint.  This is deliberately kept out of
    # the ordinary migration transaction because PRAGMA foreign_keys is a no-op
    # while a transaction is active.
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE change_transactions_observing_new (
                id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL UNIQUE,
                conversation_id TEXT
                    REFERENCES conversations(id) ON DELETE SET NULL,
                run_id TEXT
                    REFERENCES runs(id) ON DELETE SET NULL,
                state TEXT NOT NULL CHECK (
                    state IN (
                        'PENDING_APPROVAL', 'APPROVED', 'EXECUTING', 'VERIFYING',
                        'OBSERVING', 'COMPLETED', 'ROLLED_BACK', 'FAILED',
                        'QUARANTINED', 'REJECTED', 'EXPIRED'
                    )
                ),
                target_identity_json TEXT NOT NULL,
                mutation_scope TEXT NOT NULL,
                risk_level TEXT NOT NULL CHECK (
                    risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')
                ),
                intent TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                plan_digest TEXT NOT NULL,
                requested_by_actor_id TEXT NOT NULL,
                approval_expires_at TEXT NOT NULL,
                result_json TEXT NOT NULL DEFAULT '{}',
                error_code TEXT,
                error_detail TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                execution_started_at TEXT,
                verification_started_at TEXT,
                completed_at TEXT,
                receipt_acknowledged_at TEXT,
                receipt_acknowledged_by TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO change_transactions_observing_new(
                id, operation_id, conversation_id, run_id, state,
                target_identity_json, mutation_scope, risk_level, intent,
                plan_json, plan_digest, requested_by_actor_id,
                approval_expires_at, result_json, error_code, error_detail,
                created_at, updated_at, execution_started_at,
                verification_started_at, completed_at, receipt_acknowledged_at,
                receipt_acknowledged_by
            )
            SELECT
                id, operation_id, conversation_id, run_id, state,
                target_identity_json, mutation_scope, risk_level, intent,
                plan_json, plan_digest, requested_by_actor_id,
                approval_expires_at, result_json, error_code, error_detail,
                created_at, updated_at, execution_started_at,
                verification_started_at, completed_at, receipt_acknowledged_at,
                receipt_acknowledged_by
            FROM change_transactions
            """
        )
        connection.execute("DROP TABLE change_transactions")
        connection.execute(
            "ALTER TABLE change_transactions_observing_new RENAME TO change_transactions"
        )
        connection.execute(
            """
            CREATE INDEX idx_change_transactions_conversation
            ON change_transactions(conversation_id, created_at)
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_change_transactions_state
            ON change_transactions(state, updated_at)
            """
        )
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise sqlite3.IntegrityError("change transaction FK rebuild failed")
        connection.execute(
            """
            INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (?, 'change_transaction_observing_state', ?)
            """,
            (_CHANGE_TRANSACTION_OBSERVING_SCHEMA_VERSION, _utc_now()),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


class HostStore:
    """SQLite-backed authority for UI conversations and run/event state."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.instance_id = str(uuid.uuid4())
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                row["version"]
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for version, name, statements in MIGRATIONS:
                if (
                    version > _CHANGE_TRANSACTION_OBSERVING_SCHEMA_VERSION
                    and _CHANGE_TRANSACTION_OBSERVING_SCHEMA_VERSION not in applied
                ):
                    _rebuild_change_transactions_for_observing(connection)
                    applied.add(_CHANGE_TRANSACTION_OBSERVING_SCHEMA_VERSION)
                if version in applied:
                    continue
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    for statement in statements:
                        connection.execute(statement)
                    connection.execute(
                        """
                        INSERT INTO schema_migrations(version, name, applied_at)
                        VALUES (?, ?, ?)
                        """,
                        (version, name, _utc_now()),
                    )

    def ping(self) -> bool:
        try:
            with self._connection() as connection:
                value = connection.execute("SELECT 1").fetchone()
            return value is not None and value[0] == 1
        except sqlite3.Error:
            return False

    def get_host_state(self, key: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value_json FROM host_state WHERE key = ?",
                (key,),
            ).fetchone()
        return None if row is None else _json_load(str(row["value_json"]))

    def set_host_state(self, key: str, value: dict[str, Any] | None) -> None:
        if not key or len(key) > 128:
            raise ValueError("host state key must contain between 1 and 128 characters")
        with self._connection() as connection, connection:
            if value is None:
                connection.execute("DELETE FROM host_state WHERE key = ?", (key,))
                return
            connection.execute(
                """
                INSERT INTO host_state(key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, _json_dump(value), _utc_now()),
            )

    def create_conversation(
        self,
        *,
        title: str | None = None,
        workflow: str = "engineering",
        project_brief: dict[str, Any] | None = None,
        workspace_id: str | None = None,
        revision_id: str | None = None,
        engineering_session_id: str | None = None,
    ) -> Conversation:
        if workflow not in {"engineering", "new_project_planning"}:
            raise ValueError("unsupported conversation workflow")
        if workflow == "new_project_planning" and not project_brief:
            raise ValueError("new-project planning requires a project brief")
        if workflow == "engineering" and project_brief is not None:
            raise ValueError("engineering conversations cannot contain a project brief")
        target_values = (workspace_id, revision_id, engineering_session_id)
        if workflow != "engineering" and any(value is not None for value in target_values):
            raise ValueError(
                "only engineering conversations can contain a target binding"
            )
        if any(value is not None and not value.strip() for value in target_values):
            raise ValueError("conversation target binding values cannot be blank")
        if any(value is not None for value in target_values) and not all(
            value is not None for value in target_values
        ):
            raise ValueError(
                "engineering conversation target binding must be complete"
            )
        conversation_id = str(uuid.uuid4())
        timestamp = _utc_now()
        with self._connection() as connection, connection:
            connection.execute(
                """
                INSERT INTO conversations(
                    id, title, workflow, project_brief_json,
                    workspace_id, revision_id, engineering_session_id,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    title,
                    workflow,
                    _json_dump(project_brief or {}),
                    workspace_id,
                    revision_id,
                    engineering_session_id,
                    timestamp,
                    timestamp,
                ),
            )
            self._insert_event(
                connection,
                conversation_id=conversation_id,
                event_type="conversation.created",
                payload={
                    "conversation_id": conversation_id,
                    "title": title,
                    "workflow": workflow,
                    "target_bound": all(
                        value is not None for value in target_values
                    ),
                },
            )
            row = connection.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        assert row is not None
        return self._conversation_from_row(row)

    def create_engineering_handoff_conversation(
        self,
        *,
        title: str | None,
        native_project_id: str,
        planning_conversation_id: str,
        workspace_id: str,
        revision_id: str,
        engineering_session_id: str,
        provenance: dict[str, Any],
    ) -> tuple[Conversation, bool]:
        """Create one immutable planning-to-engineering handoff conversation.

        The native project id is the idempotency authority. Replays return the
        exact same target-bound conversation only when every durable input is
        unchanged.  The engineering session id is deliberately ephemeral: an
        exact immutable workspace/revision/provenance replay may atomically
        rebind it after the same protected copy is resumed in a new XG5000
        process.  A planning conversation with a completed handoff becomes a
        read-only lineage source.
        """

        if not all(
            value.strip()
            for value in (
                None,
                planning_conversation_id,
                workspace_id,
                revision_id,
                engineering_session_id,
            )
        ):
            raise ValueError("engineering handoff identity cannot be blank")
        if provenance.get("nativeProjectId") != None or (
            provenance.get("planningConversationId") != planning_conversation_id
        ):
            raise ValueError("engineering handoff provenance identity is invalid")

        timestamp = _utc_now()
        canonical_provenance = _json_dump(provenance)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = None
                if existing is not None:
                    if (
                        existing["workflow"] != "engineering"
                        or existing["lineage_planning_conversation_id"]
                        != planning_conversation_id
                        or existing["workspace_id"] != workspace_id
                        or existing["revision_id"] != revision_id
                        or str(existing["engineering_handoff_json"])
                        != canonical_provenance
                    ):
                        raise ValueError(
                            "native project handoff is already bound to different provenance"
                        )
                    prior_session_id = str(existing["engineering_session_id"] or "")
                    if prior_session_id != engineering_session_id:
                        cursor = connection.execute(
                            """
                            UPDATE conversations
                            SET engineering_session_id = ?, updated_at = ?
                            WHERE id = ?
                              AND workflow = 'engineering'
                              AND handoff_native_project_id = ?
                              AND lineage_planning_conversation_id = ?
                              AND workspace_id = ?
                              AND revision_id = ?
                              AND engineering_handoff_json = ?
                              AND engineering_session_id = ?
                            """,
                            (
                                engineering_session_id,
                                timestamp,
                                existing["id"],
                                None,
                                planning_conversation_id,
                                workspace_id,
                                revision_id,
                                canonical_provenance,
                                prior_session_id,
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise ValueError(
                                "engineering handoff changed during session rebind"
                            )
                        self._insert_event(
                            connection,
                            conversation_id=str(existing["id"]),
                            event_type="conversation.engineering_session_rebound",
                            payload={
                                "native_project_id": None,
                                "planning_conversation_id": planning_conversation_id,
                                "workspace_id": workspace_id,
                                "revision_id": revision_id,
                                "project_sha256": provenance.get("projectSha256"),
                                "previous_engineering_session_id": prior_session_id,
                                "engineering_session_id": engineering_session_id,
                            },
                        )
                        rebound = connection.execute(
                            "SELECT * FROM conversations WHERE id = ?",
                            (existing["id"],),
                        ).fetchone()
                        connection.commit()
                        assert rebound is not None
                        return self._conversation_from_row(rebound), False
                    connection.rollback()
                    return self._conversation_from_row(existing), False

                planning = connection.execute(
                    "SELECT workflow FROM conversations WHERE id = ?",
                    (planning_conversation_id,),
                ).fetchone()
                if planning is None or planning["workflow"] != "new_project_planning":
                    raise ValueError("engineering handoff planning conversation is invalid")
                active_run = connection.execute(
                    """
                    SELECT 1 FROM runs
                    WHERE conversation_id = ?
                      AND state IN (
                        'IDLE', 'ANALYZING', 'WAITING_FOR_USER',
                        'WAITING_FOR_APPROVAL', 'APPROVED_PENDING_EXECUTION',
                        'EXECUTING', 'VERIFYING'
                      )
                    LIMIT 1
                    """,
                    (planning_conversation_id,),
                ).fetchone()
                if active_run is not None:
                    raise ValueError(
                        "planning conversation still has an active run"
                    )

                conversation_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO conversations(
                        id, title, workflow, project_brief_json,
                        workspace_id, revision_id, engineering_session_id,
                        handoff_native_project_id,
                        lineage_planning_conversation_id,
                        engineering_handoff_json,
                        created_at, updated_at
                    )
                    VALUES (?, ?, 'engineering', '{}', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        conversation_id,
                        title,
                        workspace_id,
                        revision_id,
                        engineering_session_id,
                        None,
                        planning_conversation_id,
                        canonical_provenance,
                        timestamp,
                        timestamp,
                    ),
                )
                self._insert_event(
                    connection,
                    conversation_id=conversation_id,
                    event_type="conversation.created",
                    payload={
                        "conversation_id": conversation_id,
                        "title": title,
                        "workflow": "engineering",
                        "target_bound": True,
                        "native_project_id": None,
                        "planning_conversation_id": planning_conversation_id,
                    },
                )
                self._insert_event(
                    connection,
                    conversation_id=planning_conversation_id,
                    event_type="conversation.engineering_handoff_created",
                    payload={
                        "engineering_conversation_id": conversation_id,
                        "native_project_id": None,
                    },
                )
                row = connection.execute(
                    "SELECT * FROM conversations WHERE id = ?",
                    (conversation_id,),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert row is not None
        return self._conversation_from_row(row), True

    def list_conversations(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[Conversation], int]:
        with self._connection() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0])
            rows = connection.execute(
                """
                SELECT * FROM conversations
                ORDER BY updated_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return [self._conversation_from_row(row) for row in rows], total

    def get_conversation(self, conversation_id: str) -> ConversationDetail | None:
        with self._connection() as connection:
            conversation_row = connection.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if conversation_row is None:
                return None
            message_rows = connection.execute(
                """
                SELECT
                    messages.*,
                    project_plan_reviews.plan_digest AS review_plan_digest,
                    project_plan_reviews.schema_version AS review_schema_version,
                    project_plan_reviews.actor_id AS review_actor_id,
                    project_plan_reviews.reviewed_at AS review_reviewed_at,
                    project_plan_reviews.reviewed_section_ids_json
                        AS review_reviewed_section_ids_json,
                    project_plan_reviews.reviewed_item_ids_json
                        AS review_reviewed_item_ids_json,
                    project_plan_reviews.project_brief_digest
                        AS review_project_brief_digest,
                    project_plan_reviews.attachment_revision_digest
                        AS review_attachment_revision_digest
                FROM messages
                LEFT JOIN project_plan_reviews
                  ON project_plan_reviews.message_id = messages.id
                 AND project_plan_reviews.plan_digest =
                     json_extract(messages.metadata_json, '$.plan_digest')
                WHERE messages.conversation_id = ?
                ORDER BY messages.sequence ASC
                """,
                (conversation_id,),
            ).fetchall()
            run_rows = connection.execute(
                """
                SELECT * FROM runs
                WHERE conversation_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (conversation_id,),
            ).fetchall()

        conversation = self._conversation_from_row(conversation_row)
        return ConversationDetail(
            **conversation.model_dump(),
            messages=[self._message_from_row(row) for row in message_rows],
            runs=[self._run_from_row(row) for row in run_rows],
        )

    def review_project_plan(
        self,
        *,
        conversation_id: str,
        message_id: str,
        plan_digest: str,
        reviewed_section_ids: list[str],
        reviewed_item_ids: list[str],
        attachment_revision_digest: str,
        actor_id: str,
    ) -> tuple[ProjectPlanReview | None, bool]:
        """Review only the latest ready, digest-matching planning artifact."""

        if not actor_id.strip():
            raise ValueError("project plan review actor_id cannot be blank")
        if len(plan_digest) != 64 or any(
            character not in "0123456789abcdef" for character in plan_digest
        ):
            raise ValueError("project plan digest must be lowercase SHA-256")

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                conversation = connection.execute(
                    "SELECT workflow, project_brief_json FROM conversations WHERE id = ?",
                    (conversation_id,),
                ).fetchone()
                if conversation is None:
                    connection.rollback()
                    return None, False
                if conversation["workflow"] != "new_project_planning":
                    raise ProjectPlanReviewConflict(
                        "only new-project planning conversations can be reviewed"
                    )
                latest = connection.execute(
                    """
                    SELECT id, sequence, metadata_json
                    FROM messages
                    WHERE conversation_id = ?
                      AND role = 'assistant'
                      AND json_type(metadata_json, '$.plan_artifact') = 'object'
                      AND json_type(metadata_json, '$.plan_digest') = 'text'
                    ORDER BY sequence DESC
                    LIMIT 1
                    """,
                    (conversation_id,),
                ).fetchone()
                if latest is None:
                    raise ProjectPlanReviewConflict(
                        "conversation has no structured project plan to review"
                    )
                if latest["id"] != message_id:
                    raise ProjectPlanReviewConflict(
                        "project plan is stale; review the latest planning message"
                    )
                metadata = _json_load(str(latest["metadata_json"]))
                artifact = metadata.get("plan_artifact")
                stored_digest = metadata.get("plan_digest")
                if (
                    not isinstance(artifact, dict)
                    or artifact.get("schemaVersion") != "2"
                ):
                    raise ProjectPlanReviewConflict(
                        "latest project plan artifact is invalid"
                    )
                project_brief = _json_load(str(conversation["project_brief_json"]))
                if not project_brief:
                    raise ProjectPlanReviewConflict(
                        "new-project review requires the current Host project brief"
                    )
                try:
                    project_brief_digest = calculate_project_brief_digest(project_brief)
                except (TypeError, ValueError) as exc:
                    raise ProjectPlanReviewConflict(
                        "current project brief does not match the typed contract"
                    ) from exc
                if (
                    metadata.get("project_brief_digest") != project_brief_digest
                    or metadata.get("attachment_revision_digest")
                    != attachment_revision_digest
                ):
                    raise ProjectPlanReviewConflict(
                        "project plan was not generated from the current brief and source revisions"
                    )
                design = artifact.get("design")
                completeness = artifact.get("completeness")
                raise ProjectPlanReviewConflict(
                    "project plan still needs clarification"
                )
                canonical_artifact = json.dumps(
                    artifact,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
                actual_digest = hashlib.sha256(canonical_artifact).hexdigest()
                if stored_digest != actual_digest:
                    raise ProjectPlanReviewConflict(
                        "stored project plan digest does not match its artifact"
                    )
                if plan_digest != stored_digest:
                    raise ProjectPlanReviewConflict(
                        "project plan digest does not match the latest plan"
                    )
                expected_section_ids = self._project_plan_section_ids(artifact)
                supplied_section_ids = self._normalize_reviewed_section_ids(
                    reviewed_section_ids
                )
                if supplied_section_ids != expected_section_ids:
                    raise ProjectPlanReviewConflict(
                        "every project plan section must be explicitly reviewed"
                    )
                canonical_reviewed_section_ids = expected_section_ids
                try:
                    expected_item_ids = project_plan_review_item_ids(artifact)
                    requirement_messages = {
                        str(row["id"]): str(row["content"])
                        for row in connection.execute(
                            "SELECT id, content FROM messages "
                            "WHERE conversation_id = ? AND role = 'user'",
                            (conversation_id,),
                        ).fetchall()
                    }
                    validate_project_plan_scope_evidence(
                        artifact,
                        project_brief,
                        requirement_messages=requirement_messages,
                    )
                    artifact_attachment_digest = (
                        calculate_project_plan_attachment_revision_digest(
                            artifact.get("attachments", [])
                        )
                    )
                except (TypeError, ValueError) as exc:
                    raise ProjectPlanReviewConflict(
                        "project plan executable review rows are invalid"
                    ) from exc
                supplied_item_ids = self._normalize_reviewed_item_ids(
                    reviewed_item_ids
                )
                if supplied_item_ids != expected_item_ids:
                    raise ProjectPlanReviewConflict(
                        "every project plan item and executable relation must be explicitly reviewed"
                    )
                if attachment_revision_digest != artifact_attachment_digest:
                    raise ProjectPlanReviewConflict(
                        "project attachment source revisions changed before review"
                    )

                existing = connection.execute(
                    """
                    SELECT * FROM project_plan_reviews
                    WHERE message_id = ? AND plan_digest = ?
                    """,
                    (message_id, plan_digest),
                ).fetchone()
                if existing is not None:
                    if existing["schema_version"] != "2":
                        raise ProjectPlanReviewConflict(
                            "legacy section-only review cannot authorize execution; replan and review again"
                        )
                    existing_section_ids = self._reviewed_section_ids_from_row(
                        existing
                    )
                    existing_item_ids = self._reviewed_item_ids_from_row(existing)
                    if (
                        existing_section_ids != canonical_reviewed_section_ids
                        or existing_item_ids != expected_item_ids
                        or existing["project_brief_digest"] != project_brief_digest
                        or existing["attachment_revision_digest"]
                        != attachment_revision_digest
                    ):
                        raise ProjectPlanReviewConflict(
                            "stored project plan review authority no longer matches"
                        )
                    connection.rollback()
                    return self._project_plan_review_from_row(existing), False

                timestamp = _utc_now()
                connection.execute(
                    """
                    INSERT INTO project_plan_reviews(
                        message_id, plan_digest, conversation_id,
                        actor_id, reviewed_at, reviewed_section_ids_json,
                        schema_version, reviewed_item_ids_json,
                        project_brief_digest, attachment_revision_digest
                    )
                    VALUES (?, ?, ?, ?, ?, ?, '2', ?, ?, ?)
                    """,
                    (
                        message_id,
                        plan_digest,
                        conversation_id,
                        actor_id,
                        timestamp,
                        json.dumps(
                            canonical_reviewed_section_ids,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        json.dumps(
                            expected_item_ids,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        project_brief_digest,
                        attachment_revision_digest,
                    ),
                )
                self._insert_event(
                    connection,
                    conversation_id=conversation_id,
                    event_type="project_plan.reviewed",
                    payload={
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "plan_digest": plan_digest,
                        "reviewed_section_ids": canonical_reviewed_section_ids,
                        "reviewed_item_count": len(expected_item_ids),
                        "project_brief_digest": project_brief_digest,
                        "attachment_revision_digest": attachment_revision_digest,
                        "actor_id": actor_id,
                    },
                )
                row = connection.execute(
                    """
                    SELECT * FROM project_plan_reviews
                    WHERE message_id = ? AND plan_digest = ?
                    """,
                    (message_id, plan_digest),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert row is not None
        return self._project_plan_review_from_row(row), True

    def require_reviewed_project_plan(
        self,
        *,
        conversation_id: str,
        message_id: str,
        plan_digest: str,
        reviewed_section_ids: list[str],
        reviewed_item_ids: list[str],
        project_brief_digest: str,
        attachment_revision_digest: str,
        expected_handoff_native_project_id: str | None = None,
    ) -> tuple[ProjectPlanReview, dict[str, Any]]:
        """Re-read and prove the exact latest reviewed plan before creation."""

        with self._connection() as connection:
            # Serialize this authority read with handoff creation. Native allocation
            # independently repeats the lineage check in its own write transaction,
            # closing the gap between this review proof and operation allocation.
            connection.execute("BEGIN IMMEDIATE")
            conversation = connection.execute(
                "SELECT workflow, project_brief_json FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if conversation is None:
                raise ProjectPlanReviewConflict(
                    "reviewed project plan conversation was not found"
                )
            if conversation["workflow"] != "new_project_planning":
                raise ProjectPlanReviewConflict(
                    "native project creation requires a planning conversation"
                )
            current_brief = _json_load(str(conversation["project_brief_json"]))
            if not current_brief:
                raise ProjectPlanReviewConflict(
                    "native project creation requires the current Host project brief"
                )
            try:
                actual_brief_digest = calculate_project_brief_digest(current_brief)
            except (TypeError, ValueError) as exc:
                raise ProjectPlanReviewConflict(
                    "current project brief does not match the typed contract"
                ) from exc
            latest = connection.execute(
                """
                SELECT id, sequence, metadata_json
                FROM messages
                WHERE conversation_id = ?
                  AND role = 'assistant'
                  AND json_type(metadata_json, '$.plan_artifact') = 'object'
                  AND json_type(metadata_json, '$.plan_digest') = 'text'
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
            if latest is None or latest["id"] != message_id:
                raise ProjectPlanReviewConflict(
                    "reviewed project plan is stale; review the latest plan"
                )
            metadata = _json_load(str(latest["metadata_json"]))
            artifact = metadata.get("plan_artifact")
            if not isinstance(artifact, dict):
                raise ProjectPlanReviewConflict(
                    "latest project plan artifact is invalid"
                )
            design = artifact.get("design")
            completeness = artifact.get("completeness")
            actual_digest = hashlib.sha256(
                json.dumps(
                    artifact,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            raise ProjectPlanReviewConflict(
                "reviewed project plan digest or readiness no longer matches"
            )
            expected_section_ids = self._project_plan_section_ids(artifact)
            supplied_section_ids = self._normalize_reviewed_section_ids(
                reviewed_section_ids
            )
            try:
                expected_item_ids = project_plan_review_item_ids(artifact)
                requirement_messages = {
                    str(row["id"]): str(row["content"])
                    for row in connection.execute(
                        "SELECT id, content FROM messages "
                        "WHERE conversation_id = ? AND role = 'user'",
                        (conversation_id,),
                    ).fetchall()
                }
                validate_project_plan_scope_evidence(
                    artifact,
                    current_brief,
                    requirement_messages=requirement_messages,
                )
                artifact_attachment_digest = (
                    calculate_project_plan_attachment_revision_digest(
                        artifact.get("attachments", [])
                    )
                )
            except (TypeError, ValueError) as exc:
                raise ProjectPlanReviewConflict(
                    "reviewed project plan executable rows are invalid"
                ) from exc
            supplied_item_ids = self._normalize_reviewed_item_ids(reviewed_item_ids)
            if (
                metadata.get("project_brief_digest") != actual_brief_digest
                or metadata.get("attachment_revision_digest")
                != attachment_revision_digest
            ):
                raise ProjectPlanReviewConflict(
                    "reviewed project plan generation authority no longer matches"
                )
            review_row = connection.execute(
                """
                SELECT * FROM project_plan_reviews
                WHERE message_id = ? AND plan_digest = ?
                """,
                (message_id, plan_digest),
            ).fetchone()
            if review_row is None:
                raise ProjectPlanReviewConflict(
                    "latest project plan has no human review"
                )
            if review_row["schema_version"] != "2":
                raise ProjectPlanReviewConflict(
                    "legacy section-only review cannot authorize native execution"
                )
            stored_section_ids = self._reviewed_section_ids_from_row(review_row)
            stored_item_ids = self._reviewed_item_ids_from_row(review_row)
            if (
                stored_section_ids != expected_section_ids
                or supplied_section_ids != expected_section_ids
                or stored_item_ids != expected_item_ids
                or supplied_item_ids != expected_item_ids
                or project_brief_digest != actual_brief_digest
                or review_row["project_brief_digest"] != actual_brief_digest
                or attachment_revision_digest != artifact_attachment_digest
                or review_row["attachment_revision_digest"]
                != artifact_attachment_digest
            ):
                raise ProjectPlanReviewConflict(
                    "reviewed project plan authority no longer matches"
                )
            review = self._project_plan_review_from_row(review_row)
            connection.rollback()
            return review, artifact

    def get_engineering_handoff_conversation(
        self,
        native_project_id: str,
    ) -> Conversation | None:
        """Return the durable engineering handoff owned by a native operation."""

        with self._connection() as connection:
            row = None
        return self._conversation_from_row(row) if row is not None else None

    def get_engineering_mission(
        self,
        conversation_id: str,
    ) -> EngineeringMission | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM engineering_missions WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        return self._engineering_mission_from_row(row) if row is not None else None

    def get_engineering_agent_checkpoint(
        self,
        conversation_id: str,
    ) -> EngineeringAgentCheckpoint | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM engineering_agent_checkpoints WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        return self._engineering_agent_checkpoint_from_row(row) if row is not None else None

    def checkpoint_engineering_transcript(
        self, *, conversation_id: str, run_id: str,
        workspace_id: str | None, revision_id: str | None,
        checkpoint: dict[str, Any], digest: str,
    ) -> EngineeringAgentTranscript:
        """Keep original completed messages under the Host's run provenance."""
        canonical, digest, parts, users = validated_transcript_checkpoint(checkpoint, digest)
        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            conversation = connection.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if (run is None or run["conversation_id"] != conversation_id
                    or not run["request_message_id"] or conversation is None
                    or conversation["workspace_id"] != workspace_id
                    or conversation["revision_id"] != revision_id):
                raise EngineeringMissionConflict("Agent transcript run context mismatched")
            prior = connection.execute("SELECT * FROM engineering_agent_transcripts WHERE source_run_id = ?", (run_id,)).fetchone()
            if prior is not None:
                _, _, old_parts, old_users = validated_transcript_checkpoint(
                    _json_load(prior["checkpoint_json"]), prior["digest"])
                old_text = [part for message in _json_load(prior["checkpoint_json"])["messages"]
                            for part in message["parts"] if part["part_kind"] == "text"]
                text = [part for message in canonical["messages"]
                        for part in message["parts"] if part["part_kind"] == "text"]
                if (prior["conversation_id"] != conversation_id
                        or prior["workspace_id"] != workspace_id or prior["revision_id"] != revision_id
                        or prior["request_message_id"] != run["request_message_id"]
                        or parts[:len(old_parts)] != old_parts or users[:len(old_users)] != old_users
                        or text[:len(old_text)] != old_text):
                    raise EngineeringMissionConflict("Agent transcript cursor regressed or changed")
            connection.execute(
                """INSERT INTO engineering_agent_transcripts
                (source_run_id, conversation_id, workspace_id, revision_id, request_message_id,
                 cursor, checkpoint_json, digest, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_run_id) DO UPDATE SET cursor=excluded.cursor,
                checkpoint_json=excluded.checkpoint_json, digest=excluded.digest,
                updated_at=excluded.updated_at""",
                (run_id, conversation_id, workspace_id, revision_id, run["request_message_id"],
                 len(parts), _json_dump(canonical), digest, prior["created_at"] if prior else now, now),
            )
            row = connection.execute("SELECT * FROM engineering_agent_transcripts WHERE source_run_id = ?", (run_id,)).fetchone()
            connection.commit()
        return self._engineering_agent_transcript_from_row(row)

    def get_latest_engineering_transcript(
        self, conversation_id: str, *, workspace_id: str | None, revision_id: str | None,
    ) -> EngineeringAgentTranscript | None:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT * FROM engineering_agent_transcripts
                WHERE conversation_id = ? AND workspace_id IS ? AND revision_id IS ?
                ORDER BY updated_at DESC, source_run_id DESC LIMIT 1""",
                (conversation_id, workspace_id, revision_id),
            ).fetchone()
        return self._engineering_agent_transcript_from_row(row) if row else None

    def reverify_blocked_engineering_mission_step(
        self,
        *,
        conversation_id: str,
        operation_id: str,
        completed_report: bool = False,
    ) -> tuple[EngineeringMission, bool]:
        """Explicitly reopen one blocked step or one completed final report.

        The transition is intentionally narrow: completed steps and every
        evidence reference remain immutable, while only the selected blocked
        step's blocker outcome is cleared.  The existing Agent checkpoint keeps
        its messages/cycle and is re-digested solely for the new mission digest.
        """

        timestamp = _utc_now()
        event_type = (
            "mission.completed_report_reverified"
            if completed_report
            else "mission.blocked_step_reverified"
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                prior_event = connection.execute(
                    """
                    SELECT conversation_id FROM events
                    WHERE event_type = ?
                      AND json_extract(payload_json, '$.operationId') = ?
                    ORDER BY sequence ASC LIMIT 1
                    """,
                    (event_type, operation_id),
                ).fetchone()
                if prior_event is not None:
                    if prior_event["conversation_id"] != conversation_id:
                        raise EngineeringMissionConflict(
                            "operationId belongs to another conversation"
                        )
                    row = connection.execute(
                        "SELECT * FROM engineering_missions WHERE conversation_id = ?",
                        (conversation_id,),
                    ).fetchone()
                    if row is None:
                        raise EngineeringMissionConflict("mission conversation was not found")
                    connection.rollback()
                    return self._engineering_mission_from_row(row), False

                row = connection.execute(
                    "SELECT * FROM engineering_missions WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()
                if row is None:
                    raise EngineeringMissionConflict("conversation has no durable engineering mission")
                snapshot = _json_load(str(row["snapshot_json"]))
                expected_status = "completed" if completed_report else "blocked"
                if snapshot.get("status") != expected_status:
                    raise EngineeringMissionConflict(
                        "the engineering mission is not in the requested reverify state"
                    )
                try:
                    snapshot, previous_canonical_digest = (
                        canonical_engineering_mission_snapshot(snapshot)
                    )
                except (TypeError, ValueError) as exc:
                    raise EngineeringMissionConflict(
                        "stored engineering mission is invalid"
                    ) from exc
                steps = snapshot.get("steps")
                if not isinstance(steps, list) or not steps:
                    raise EngineeringMissionConflict("stored engineering mission steps are invalid")
                selected_step: dict[str, Any] | None
                if completed_report:
                    final_step = steps[-1] if isinstance(steps[-1], dict) else None
                    final_title = str(final_step.get("title") or "") if final_step else ""
                    selected_step = (
                        final_step
                        if final_step is not None
                        and final_step.get("status") == "completed"
                        and not final_step.get("evidenceRefs")
                        and (
                            "최종" in final_title and "보고" in final_title
                            or "final" in final_title.casefold()
                            and "report" in final_title.casefold()
                        )
                        else None
                    )
                else:
                    selected_step = next(
                        (
                            step
                            for step in steps
                            if isinstance(step, dict) and step.get("status") == "blocked"
                        ),
                        None,
                    )
                    if selected_step is None:
                        selected_step = next(
                            (
                                step
                                for step in steps
                                if isinstance(step, dict)
                                and step.get("status") != "completed"
                            ),
                            None,
                        )
                if (
                    selected_step is None
                    or not isinstance(selected_step.get("stepId"), str)
                    or not selected_step["stepId"]
                ):
                    raise EngineeringMissionConflict("mission has no reverifyable step")

                previous_digest = str(row["digest"])
                if previous_canonical_digest != previous_digest:
                    raise EngineeringMissionConflict("stored engineering mission digest does not match")
                selected_step_id = selected_step["stepId"]
                selected_step["status"] = "active"
                # Keep its existing evidence (for example, a successful read)
                # because the false-negative is the blocker interpretation, not
                # evidence invalidation.  Only that blocker outcome is cleared.
                selected_step["outcome"] = None
                snapshot["status"] = "active"
                snapshot["activeStepId"] = selected_step_id
                snapshot["blockingQuestions"] = []
                snapshot["nextAction"] = (
                    "Re-render the explicitly reopened completed final report from retained evidence."
                    if completed_report
                    else "Re-verify the explicitly reopened blocked step."
                )
                snapshot["stopReason"] = "continuing"
                snapshot, digest = canonical_engineering_mission_snapshot(snapshot)
                canonical_snapshot = _json_dump(snapshot)

                checkpoint_row = connection.execute(
                    "SELECT * FROM engineering_agent_checkpoints WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()
                if checkpoint_row is None:
                    raise EngineeringMissionConflict("mission has no compatible Agent checkpoint")
                checkpoint = _json_load(str(checkpoint_row["checkpoint_json"]))
                if (
                    not isinstance(checkpoint, dict)
                    or checkpoint.get("schemaVersion") != "1"
                    or checkpoint.get("missionDigest") != previous_digest
                    or not isinstance(checkpoint.get("cycle"), int)
                    or not isinstance(checkpoint.get("messages"), list)
                ):
                    raise EngineeringMissionConflict("stored Agent checkpoint is incompatible")
                checkpoint["missionDigest"] = digest
                checkpoint_json = _json_dump(checkpoint)
                checkpoint_digest = hashlib.sha256(checkpoint_json.encode("utf-8")).hexdigest()
                next_revision = int(row["revision"]) + 1
                connection.execute(
                    """
                    UPDATE engineering_missions
                    SET revision = ?, snapshot_json = ?, digest = ?, updated_at = ?
                    WHERE conversation_id = ? AND revision = ? AND digest = ?
                    """,
                    (
                        next_revision,
                        canonical_snapshot,
                        digest,
                        timestamp,
                        conversation_id,
                        int(row["revision"]),
                        previous_digest,
                    ),
                )
                connection.execute(
                    """
                    UPDATE engineering_agent_checkpoints
                    SET mission_digest = ?, checkpoint_json = ?, digest = ?, updated_at = ?
                    WHERE conversation_id = ?
                    """,
                    (digest, checkpoint_json, checkpoint_digest, timestamp, conversation_id),
                )
                self._insert_event(
                    connection,
                    conversation_id=conversation_id,
                    event_type=event_type,
                    payload={
                        "conversationId": conversation_id,
                        "operationId": operation_id,
                        "previousRevision": int(row["revision"]),
                        "revision": next_revision,
                        "previousDigest": previous_digest,
                        "digest": digest,
                        "stepId": selected_step_id,
                        "automaticRetry": False,
                    },
                )
                updated = connection.execute(
                    "SELECT * FROM engineering_missions WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert updated is not None
        return self._engineering_mission_from_row(updated), True

    def repair_buggy_reverified_engineering_mission_digests(self) -> int:
        """Repair only the short-lived reverify null-field digest defect.

        The repair is deliberately bounded to a mission whose most recent
        mission event is the affected explicit reverify event and whose row and
        checkpoint all still agree on the same non-canonical digest.  It never
        changes a mission snapshot, revision, evidence, cycle, or messages.
        """

        timestamp = _utc_now()
        repaired = 0
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                candidates = connection.execute(
                    """
                    SELECT mission.*,
                           checkpoint.mission_digest AS checkpoint_mission_digest,
                           checkpoint.checkpoint_json,
                           checkpoint.digest AS checkpoint_digest
                    FROM engineering_missions AS mission
                    JOIN engineering_agent_checkpoints AS checkpoint
                      ON checkpoint.conversation_id = mission.conversation_id
                    WHERE mission.snapshot_json LIKE '%\"outcome\":null%'
                    """
                ).fetchall()
                for row in candidates:
                    latest_event = connection.execute(
                        """
                        SELECT event_type, payload_json FROM events
                        WHERE conversation_id = ?
                        ORDER BY sequence DESC LIMIT 1
                        """,
                        (row["conversation_id"],),
                    ).fetchone()
                    if (
                        latest_event is None
                        or latest_event["event_type"]
                        != "mission.blocked_step_reverified"
                    ):
                        continue
                    try:
                        snapshot, canonical_digest = canonical_engineering_mission_snapshot(
                            _json_load(str(row["snapshot_json"]))
                        )
                        checkpoint = _json_load(str(row["checkpoint_json"]))
                    except (TypeError, ValueError):
                        continue
                    old_digest = str(row["digest"])
                    if (
                        old_digest == canonical_digest
                        or str(row["checkpoint_mission_digest"]) != old_digest
                        or checkpoint.get("missionDigest") != old_digest
                        or hashlib.sha256(
                            _json_dump(checkpoint).encode("utf-8")
                        ).hexdigest()
                        != str(row["checkpoint_digest"])
                        or not isinstance(checkpoint.get("cycle"), int)
                        or not isinstance(checkpoint.get("messages"), list)
                    ):
                        continue
                    # The old value must be exactly the historic raw JSON hash;
                    # otherwise this is not the known defect and must fail closed.
                    raw_snapshot = _json_load(str(row["snapshot_json"]))
                    if hashlib.sha256(_json_dump(raw_snapshot).encode("utf-8")).hexdigest() != old_digest:
                        continue
                    checkpoint["missionDigest"] = canonical_digest
                    checkpoint_json = _json_dump(checkpoint)
                    checkpoint_digest = hashlib.sha256(
                        checkpoint_json.encode("utf-8")
                    ).hexdigest()
                    snapshot_json = _json_dump(snapshot)
                    mission_updated = connection.execute(
                        """
                        UPDATE engineering_missions
                        SET snapshot_json = ?, digest = ?, updated_at = ?
                        WHERE conversation_id = ? AND revision = ? AND digest = ?
                        """,
                        (
                            snapshot_json,
                            canonical_digest,
                            timestamp,
                            row["conversation_id"],
                            row["revision"],
                            old_digest,
                        ),
                    ).rowcount
                    checkpoint_updated = connection.execute(
                        """
                        UPDATE engineering_agent_checkpoints
                        SET mission_digest = ?, checkpoint_json = ?, digest = ?, updated_at = ?
                        WHERE conversation_id = ? AND mission_digest = ?
                        """,
                        (
                            canonical_digest,
                            checkpoint_json,
                            checkpoint_digest,
                            timestamp,
                            row["conversation_id"],
                            old_digest,
                        ),
                    ).rowcount
                    if mission_updated != 1 or checkpoint_updated != 1:
                        raise EngineeringMissionConflict(
                            "reverify mission digest repair conflicted"
                        )
                    self._insert_event(
                        connection,
                        conversation_id=str(row["conversation_id"]),
                        event_type="mission.recovery_digest_repaired",
                        payload={
                            "conversationId": row["conversation_id"],
                            "revision": row["revision"],
                            "previousDigest": old_digest,
                            "digest": canonical_digest,
                        },
                    )
                    repaired += 1
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return repaired

    def checkpoint_engineering_mission(
        self,
        *,
        conversation_id: str,
        run_id: str,
        cycle: int,
        cycle_disposition: str,
        engineering_mission: dict[str, Any],
        digest: str,
        agent_checkpoint: dict[str, Any],
        agent_checkpoint_digest: str,
        change_transaction_id: str | None = None,
        supplemental_evidence: dict[str, Any] | None = None,
        final_message_content: str | None = None,
        final_message_metadata: dict[str, Any] | None = None,
    ) -> tuple[EngineeringMission, Message | None]:
        """Retain a mission, atomically with its final answer when responding."""

        if (
            isinstance(cycle, bool)
            or not isinstance(cycle, int)
            or cycle < 1
            or cycle_disposition not in {"continue", "respond", "checkpoint"}
        ):
            raise EngineeringMissionConflict("mission checkpoint cycle is invalid")
        has_final_message = (
            final_message_content is not None and final_message_metadata is not None
        )
        if (final_message_content is None) != (final_message_metadata is None):
            raise EngineeringMissionConflict("final mission message is incomplete")
        if (cycle_disposition == "respond") != has_final_message:
            raise EngineeringMissionConflict(
                "responding mission checkpoint requires its atomic final message"
            )
        try:
            engineering_mission, actual_digest = canonical_engineering_mission_snapshot(
                engineering_mission
            )
        except (TypeError, ValueError) as exc:
            raise EngineeringMissionConflict("engineering mission checkpoint is invalid") from exc
        canonical_snapshot = _json_dump(engineering_mission)
        if digest != actual_digest:
            raise EngineeringMissionConflict("mission checkpoint digest does not match")
        checkpoint_json = _json_dump(agent_checkpoint)
        actual_checkpoint_digest = hashlib.sha256(
            checkpoint_json.encode("utf-8")
        ).hexdigest()
        if (
            agent_checkpoint_digest != actual_checkpoint_digest
            or set(agent_checkpoint) != {
                "schemaVersion",
                "cycle",
                "missionDigest",
                "messages",
            }
            or agent_checkpoint.get("schemaVersion") != "1"
            or agent_checkpoint.get("cycle") != cycle
            or agent_checkpoint.get("missionDigest") != digest
            or not isinstance(agent_checkpoint.get("messages"), list)
        ):
            raise EngineeringMissionConflict("Agent checkpoint contract is invalid")

        timestamp = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                conversation = connection.execute(
                    """
                    SELECT workflow, workspace_id, revision_id
                    FROM conversations WHERE id = ?
                    """,
                    (conversation_id,),
                ).fetchone()
                if conversation is None:
                    raise EngineeringMissionConflict("mission conversation was not found")
                if conversation["workflow"] != "engineering":
                    raise EngineeringMissionConflict(
                        "engineering missions require an engineering conversation"
                    )
                workspace_id = str(conversation["workspace_id"] or "")
                revision_id = str(conversation["revision_id"] or "")
                if not workspace_id or not revision_id:
                    raise EngineeringMissionConflict(
                        "engineering mission requires a durable target binding"
                    )
                run = connection.execute(
                    "SELECT conversation_id FROM runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
                if run is None or run["conversation_id"] != conversation_id:
                    raise EngineeringMissionConflict(
                        "mission checkpoint run does not belong to the conversation"
                    )
                linked_change = None
                if change_transaction_id is not None:
                    linked_change = connection.execute(
                        """
                        SELECT *
                        FROM change_transactions WHERE id = ?
                        """,
                        (change_transaction_id,),
                    ).fetchone()
                    if (
                        linked_change is None
                        or linked_change["conversation_id"] != conversation_id
                    ):
                        raise EngineeringMissionConflict(
                            "mission change transaction does not belong to the conversation"
                        )
                mission_status = engineering_mission.get("status")
                if cycle_disposition == "checkpoint":
                    # Transcript persistence does not renew an expired or terminal approval.
                    # The unchanged mission snapshot is checked separately below.
                    pass
                elif mission_status == "waiting_for_approval":
                    if (
                        linked_change is None
                        or linked_change["state"]
                        != ChangeTransactionState.PENDING_APPROVAL.value
                    ):
                        raise EngineeringMissionConflict(
                            "approval-waiting mission requires its exact pending change"
                        )
                elif linked_change is not None and linked_change["state"] not in {
                    ChangeTransactionState.COMPLETED.value,
                    ChangeTransactionState.ROLLED_BACK.value,
                    ChangeTransactionState.FAILED.value,
                    ChangeTransactionState.REJECTED.value,
                    ChangeTransactionState.EXPIRED.value,
                }:
                    raise EngineeringMissionConflict(
                        "non-waiting mission can retain only a terminal change outcome"
                    )
                previous_checkpoint = connection.execute(
                    """
                    SELECT payload_json FROM events
                    WHERE conversation_id = ? AND run_id = ?
                      AND event_type = 'mission.checkpointed'
                    ORDER BY sequence DESC
                    LIMIT 1
                    """,
                    (conversation_id, run_id),
                ).fetchone()
                if previous_checkpoint is None:
                    if cycle != 1:
                        raise EngineeringMissionConflict(
                            "first mission checkpoint cycle must be 1"
                        )
                else:
                    previous_payload = _json_load(
                        str(previous_checkpoint["payload_json"])
                    )
                    previous_cycle = previous_payload.get("cycle")
                    previous_disposition = previous_payload.get("cycleDisposition")
                    if (
                        isinstance(previous_cycle, bool)
                        or not isinstance(previous_cycle, int)
                        or previous_cycle < 1
                        or previous_disposition not in {"continue", "respond"}
                    ):
                        raise EngineeringMissionConflict(
                            "stored mission checkpoint sequence is invalid"
                        )
                    if previous_disposition != "continue":
                        raise EngineeringMissionConflict(
                            "mission checkpoint cannot follow a responding checkpoint"
                        )
                    if cycle != previous_cycle + 1:
                        raise EngineeringMissionConflict(
                            "mission checkpoint cycle is out of sequence"
                        )

                existing = connection.execute(
                    "SELECT * FROM engineering_missions WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()
                previous_refs: set[str] = set()
                if cycle_disposition == "checkpoint" and (
                    existing is None
                    or _json_load(str(existing["snapshot_json"])) != engineering_mission
                    or existing["digest"] != digest
                    or existing["change_transaction_id"] != change_transaction_id
                ):
                    raise EngineeringMissionConflict(
                        "transcript checkpoint cannot change the engineering mission"
                    )
                if existing is not None:
                    if (
                        existing["workspace_id"] != workspace_id
                        or existing["revision_id"] != revision_id
                    ):
                        raise EngineeringMissionConflict(
                            "stored mission target no longer matches the conversation"
                        )
                    previous_refs = self._engineering_mission_evidence_refs(
                        _json_load(str(existing["snapshot_json"]))
                    )

                current_run_refs: set[str] = set()
                evidence_rows = connection.execute(
                    """
                    SELECT payload_json FROM events
                    WHERE conversation_id = ? AND run_id = ?
                      AND event_type = 'evidence.added'
                    """,
                    (conversation_id, run_id),
                ).fetchall()
                for evidence_row in evidence_rows:
                    evidence = _json_load(str(evidence_row["payload_json"]))
                    evidence_id = evidence.get("evidenceId")
                    if isinstance(evidence_id, str) and evidence_id:
                        current_run_refs.add(evidence_id)
                checkpoint_refs = self._engineering_mission_evidence_refs(
                    engineering_mission
                )
                if supplemental_evidence is not None:
                    if linked_change is None:
                        raise EngineeringMissionConflict(
                            "supplemental mission evidence requires its linked change"
                        )
                    expected_evidence = change_outcome_evidence(
                        change_outcome_projection(
                            self._change_transaction_from_row(linked_change, None)
                        )
                    )
                    evidence_id = expected_evidence["evidenceId"]
                    if supplemental_evidence != expected_evidence:
                        raise EngineeringMissionConflict(
                            "supplemental mission evidence differs from its linked change"
                        )
                    if evidence_id in checkpoint_refs:
                        existing_evidence_rows = connection.execute(
                            """
                            SELECT events.* FROM events
                            INNER JOIN runs
                              ON runs.id = events.run_id
                             AND runs.conversation_id = events.conversation_id
                            WHERE events.conversation_id = ?
                              AND events.event_type = 'evidence.added'
                              AND json_extract(events.payload_json, '$.evidenceId') = ?
                            ORDER BY events.sequence ASC
                            """,
                            (conversation_id, evidence_id),
                        ).fetchall()
                        if existing_evidence_rows:
                            existing_events = [
                                self._event_from_row(row)
                                for row in existing_evidence_rows
                            ]
                            origin = existing_events[0]
                            if origin.payload != expected_evidence or any(
                                event.run_id != origin.run_id
                                or event.payload != origin.payload
                                for event in existing_events[1:]
                            ):
                                raise EngineeringMissionConflict(
                                    "supplemental mission evidence id was rebound"
                                )
                        else:
                            self._insert_event(
                                connection,
                                conversation_id=conversation_id,
                                run_id=run_id,
                                event_type="evidence.added",
                                payload=expected_evidence,
                            )
                        current_run_refs.add(evidence_id)
                if not checkpoint_refs.issubset(previous_refs | current_run_refs):
                    raise EngineeringMissionConflict(
                        "mission checkpoint cites evidence outside its durable history"
                    )

                next_revision = int(existing["revision"]) + 1 if existing is not None else 1
                created_at = str(existing["created_at"]) if existing is not None else timestamp
                connection.execute(
                    """
                    INSERT INTO engineering_missions(
                        conversation_id, workspace_id, revision_id, revision,
                        snapshot_json, digest, updated_by_run_id,
                        change_transaction_id, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(conversation_id) DO UPDATE SET
                        workspace_id = excluded.workspace_id,
                        revision_id = excluded.revision_id,
                        revision = excluded.revision,
                        snapshot_json = excluded.snapshot_json,
                        digest = excluded.digest,
                        updated_by_run_id = excluded.updated_by_run_id,
                        change_transaction_id = excluded.change_transaction_id,
                        updated_at = excluded.updated_at
                    """,
                    (
                        conversation_id,
                        workspace_id,
                        revision_id,
                        next_revision,
                        canonical_snapshot,
                        digest,
                        run_id,
                        change_transaction_id,
                        created_at,
                        timestamp,
                    ),
                )
                existing_agent_checkpoint = connection.execute(
                    """
                    SELECT created_at FROM engineering_agent_checkpoints
                    WHERE conversation_id = ?
                    """,
                    (conversation_id,),
                ).fetchone()
                checkpoint_created_at = (
                    str(existing_agent_checkpoint["created_at"])
                    if existing_agent_checkpoint is not None
                    else timestamp
                )
                connection.execute(
                    """
                    INSERT INTO engineering_agent_checkpoints(
                        conversation_id, workspace_id, revision_id, mission_digest,
                        cycle, checkpoint_json, digest, updated_by_run_id,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(conversation_id) DO UPDATE SET
                        workspace_id = excluded.workspace_id,
                        revision_id = excluded.revision_id,
                        mission_digest = excluded.mission_digest,
                        cycle = excluded.cycle,
                        checkpoint_json = excluded.checkpoint_json,
                        digest = excluded.digest,
                        updated_by_run_id = excluded.updated_by_run_id,
                        updated_at = excluded.updated_at
                    """,
                    (
                        conversation_id,
                        workspace_id,
                        revision_id,
                        digest,
                        cycle,
                        checkpoint_json,
                        agent_checkpoint_digest,
                        run_id,
                        checkpoint_created_at,
                        timestamp,
                    ),
                )
                if cycle_disposition != "checkpoint":
                    self._insert_event(
                        connection,
                        conversation_id=conversation_id,
                        run_id=run_id,
                        event_type="mission.checkpointed",
                        payload={
                            "conversationId": conversation_id,
                            "runId": run_id,
                            "revision": next_revision,
                            "digest": digest,
                            "cycle": cycle,
                            "cycleDisposition": cycle_disposition,
                            "status": engineering_mission.get("status"),
                            "activeStepId": engineering_mission.get("activeStepId"),
                        },
                    )
                self._insert_event(
                    connection,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    event_type="agent.checkpointed",
                    payload={
                        "conversationId": conversation_id,
                        "runId": run_id,
                        "cycle": cycle,
                        "digest": agent_checkpoint_digest,
                        "missionDigest": digest,
                    },
                )
                final_message_row = None
                if final_message_content is not None:
                    message_id = str(uuid.uuid4())
                    message_sequence = int(
                        connection.execute(
                            """
                            SELECT COALESCE(MAX(sequence), 0) + 1
                            FROM messages
                            WHERE conversation_id = ?
                            """,
                            (conversation_id,),
                        ).fetchone()[0]
                    )
                    connection.execute(
                        """
                        INSERT INTO messages(
                            id, conversation_id, sequence, role, content,
                            metadata_json, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            message_id,
                            conversation_id,
                            message_sequence,
                            MessageRole.ASSISTANT.value,
                            final_message_content,
                            _json_dump(final_message_metadata),
                            timestamp,
                        ),
                    )
                    connection.execute(
                        "UPDATE conversations SET updated_at = ? WHERE id = ?",
                        (timestamp, conversation_id),
                    )
                    self._insert_event(
                        connection,
                        conversation_id=conversation_id,
                        event_type="message.appended",
                        payload={
                            "conversation_id": conversation_id,
                            "message_id": message_id,
                            "message_sequence": message_sequence,
                            "role": MessageRole.ASSISTANT.value,
                        },
                    )
                    final_message_row = connection.execute(
                        "SELECT * FROM messages WHERE id = ?",
                        (message_id,),
                    ).fetchone()
                row = connection.execute(
                    "SELECT * FROM engineering_missions WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert row is not None
        final_message = (
            self._message_from_row(final_message_row)
            if final_message_row is not None
            else None
        )
        return self._engineering_mission_from_row(row), final_message

    def rebind_engineering_conversation_session(
        self,
        *,
        conversation_id: str,
        workspace_id: str,
        revision_id: str,
        engineering_session_id: str,
        reason: str = "engineering_mission_resume",
    ) -> Conversation:
        """Rebind only the ephemeral session of an exact durable target."""

        if not all(
            value.strip()
            for value in (
                conversation_id,
                workspace_id,
                revision_id,
                engineering_session_id,
            )
        ):
            raise EngineeringMissionConflict("mission resume target cannot be blank")
        if reason not in {
            "engineering_message_submission",
            "engineering_mission_resume",
            "offline_candidate_preparation",
        }:
            raise EngineeringMissionConflict("mission session rebind reason is invalid")
        timestamp = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM conversations WHERE id = ?",
                    (conversation_id,),
                ).fetchone()
                if row is None or row["workflow"] != "engineering":
                    raise EngineeringMissionConflict(
                        "mission resume conversation is not engineering"
                    )
                if row["workspace_id"] != workspace_id or row["revision_id"] != revision_id:
                    raise EngineeringMissionConflict(
                        "mission resume target differs from the conversation"
                    )
                mission = connection.execute(
                    """
                    SELECT workspace_id, revision_id
                    FROM engineering_missions
                    WHERE conversation_id = ?
                    """,
                    (conversation_id,),
                ).fetchone()
                if mission is None and reason == "engineering_mission_resume":
                    raise EngineeringMissionConflict(
                        "mission resume requires an existing durable mission"
                    )
                if mission is not None and (
                    mission["workspace_id"] != workspace_id
                    or mission["revision_id"] != revision_id
                ):
                    raise EngineeringMissionConflict(
                        "mission resume target differs from the durable mission"
                    )
                previous_session_id = str(row["engineering_session_id"] or "")
                if previous_session_id != engineering_session_id:
                    cursor = connection.execute(
                        """
                        UPDATE conversations
                        SET engineering_session_id = ?, updated_at = ?
                        WHERE id = ? AND workflow = 'engineering'
                          AND workspace_id = ? AND revision_id = ?
                          AND COALESCE(engineering_session_id, '') = ?
                        """,
                        (
                            engineering_session_id,
                            timestamp,
                            conversation_id,
                            workspace_id,
                            revision_id,
                            previous_session_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise EngineeringMissionConflict(
                            "mission conversation changed during session rebind"
                        )
                self._insert_event(
                    connection,
                    conversation_id=conversation_id,
                    event_type="conversation.engineering_session_rebound",
                    payload={
                        "conversationId": conversation_id,
                        "workspaceId": workspace_id,
                        "revisionId": revision_id,
                        "previousEngineeringSessionId": previous_session_id,
                        "engineeringSessionId": engineering_session_id,
                        "changed": previous_session_id != engineering_session_id,
                        "reason": reason,
                    },
                )
                rebound = connection.execute(
                    "SELECT * FROM conversations WHERE id = ?",
                    (conversation_id,),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert rebound is not None
        return self._conversation_from_row(rebound)

    def append_message(
        self,
        *,
        conversation_id: str,
        role: MessageRole,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> Message | None:
        message_id = str(uuid.uuid4())
        timestamp = _utc_now()
        metadata = metadata or {}
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                exists = connection.execute(
                    "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
                ).fetchone()
                if exists is None:
                    connection.rollback()
                    return None
                sequence = int(
                    connection.execute(
                        """
                        SELECT COALESCE(MAX(sequence), 0) + 1
                        FROM messages
                        WHERE conversation_id = ?
                        """,
                        (conversation_id,),
                    ).fetchone()[0]
                )
                connection.execute(
                    """
                    INSERT INTO messages(
                        id, conversation_id, sequence, role, content,
                        metadata_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        conversation_id,
                        sequence,
                        role.value,
                        content,
                        _json_dump(metadata),
                        timestamp,
                    ),
                )
                connection.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (timestamp, conversation_id),
                )
                self._insert_event(
                    connection,
                    conversation_id=conversation_id,
                    event_type="message.appended",
                    payload={
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "message_sequence": sequence,
                        "role": role.value,
                    },
                )
                row = connection.execute(
                    "SELECT * FROM messages WHERE id = ?", (message_id,)
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert row is not None
        return self._message_from_row(row)

    def create_user_message_and_run(
        self,
        *,
        conversation_id: str,
        content: str,
        operation_id: str,
        workflow: str = "engineering",
        project_brief: dict[str, Any] | None = None,
        message_metadata: dict[str, Any] | None = None,
        message_role: MessageRole = MessageRole.USER,
        run_metadata: dict[str, Any] | None = None,
    ) -> tuple[Message | None, Run | None, bool]:
        """Atomically append one request message and its idempotent analysis run."""

        if message_role not in {MessageRole.USER, MessageRole.SYSTEM}:
            raise ValueError("run request message role must be user or system")

        message_metadata = message_metadata or {}
        run_metadata = run_metadata or {}
        message_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        timestamp = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing_row = connection.execute(
                    "SELECT * FROM runs WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if existing_row is not None:
                    if existing_row["conversation_id"] == conversation_id:
                        conversation_row = connection.execute(
                            "SELECT workflow FROM conversations WHERE id = ?",
                            (conversation_id,),
                        ).fetchone()
                        if (
                            conversation_row is not None
                            and conversation_row["workflow"] != workflow
                        ):
                            raise ConversationWorkflowConflict(
                                "message workflow does not match conversation workflow"
                            )
                    connection.rollback()
                    return None, self._run_from_row(existing_row), False

                conversation_row = connection.execute(
                    "SELECT workflow FROM conversations WHERE id = ?",
                    (conversation_id,),
                ).fetchone()
                if conversation_row is None:
                    connection.rollback()
                    return None, None, False
                if conversation_row["workflow"] != workflow:
                    raise ConversationWorkflowConflict(
                        "message workflow does not match conversation workflow"
                    )
                if project_brief is not None and workflow != "new_project_planning":
                    raise ConversationWorkflowConflict(
                        "project brief updates require new_project_planning"
                    )
                active_run = connection.execute(
                    """
                    SELECT id, conversation_id FROM runs
                    WHERE state IN (
                        'IDLE', 'ANALYZING', 'WAITING_FOR_USER',
                        'WAITING_FOR_APPROVAL', 'APPROVED_PENDING_EXECUTION',
                        'EXECUTING', 'VERIFYING'
                      )
                    ORDER BY created_at ASC, id ASC
                    LIMIT 1
                    """
                ).fetchone()
                if active_run is not None:
                    raise ConversationActiveRunConflict(
                        "ControlStudio already has active run "
                        f"{active_run['id']} for conversation "
                        f"{active_run['conversation_id']}"
                    )
                message_sequence = int(
                    connection.execute(
                        """
                        SELECT COALESCE(MAX(sequence), 0) + 1
                        FROM messages
                        WHERE conversation_id = ?
                        """,
                        (conversation_id,),
                    ).fetchone()[0]
                )
                connection.execute(
                    """
                    INSERT INTO messages(
                        id, conversation_id, sequence, role, content,
                        metadata_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        conversation_id,
                        message_sequence,
                        message_role.value,
                        content,
                        _json_dump(message_metadata),
                        timestamp,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO runs(
                        id, conversation_id, operation_id, state,
                        request_message_id, metadata_json, error_code,
                        error_detail, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                    """,
                    (
                        run_id,
                        conversation_id,
                        operation_id,
                        RunState.ANALYZING.value,
                        message_id,
                        _json_dump(run_metadata),
                        timestamp,
                        timestamp,
                    ),
                )
                if project_brief is None:
                    connection.execute(
                        "UPDATE conversations SET updated_at = ? WHERE id = ?",
                        (timestamp, conversation_id),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE conversations
                        SET project_brief_json = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (_json_dump(project_brief), timestamp, conversation_id),
                    )
                self._insert_event(
                    connection,
                    conversation_id=conversation_id,
                    event_type="message.appended",
                    payload={
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "message_sequence": message_sequence,
                        "role": message_role.value,
                    },
                )
                self._insert_event(
                    connection,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    event_type="run.created",
                    payload={
                        "conversation_id": conversation_id,
                        "run_id": run_id,
                        "state": RunState.ANALYZING.value,
                        "operation_id": operation_id,
                    },
                )
                message_row = connection.execute(
                    "SELECT * FROM messages WHERE id = ?",
                    (message_id,),
                ).fetchone()
                run_row = connection.execute(
                    "SELECT * FROM runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert message_row is not None and run_row is not None
        return (
            self._message_from_row(message_row),
            self._run_from_row(run_row),
            True,
        )

    def reconcile_interrupted_runs(self) -> int:
        """Fail or quarantine non-terminal runs left by a prior Host process."""

        terminal = {
            RunState.COMPLETED.value,
            RunState.CANCELLED.value,
            RunState.FAILED.value,
            RunState.QUARANTINED.value,
        }
        uncertain_mutation_states = {
            RunState.APPROVED_PENDING_EXECUTION.value,
            RunState.EXECUTING.value,
            RunState.VERIFYING.value,
        }
        timestamp = _utc_now()
        reconciled = 0
        with self._connection() as connection, connection:
            rows = connection.execute("SELECT * FROM runs").fetchall()
            for row in rows:
                previous_state = str(row["state"])
                if previous_state in terminal:
                    continue
                next_state = (
                    RunState.QUARANTINED.value
                    if previous_state in uncertain_mutation_states
                    else RunState.FAILED.value
                )
                error_code = (
                    "interrupted_state_uncertain"
                    if next_state == RunState.QUARANTINED.value
                    else "host_restarted"
                )
                error_detail = (
                    "Host restarted while execution outcome was uncertain."
                    if next_state == RunState.QUARANTINED.value
                    else "Host restarted before this read-only run completed."
                )
                connection.execute(
                    """
                    UPDATE runs
                    SET state = ?, error_code = ?, error_detail = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (next_state, error_code, error_detail, timestamp, row["id"]),
                )
                self._insert_event(
                    connection,
                    conversation_id=row["conversation_id"],
                    run_id=row["id"],
                    event_type="run.state_changed",
                    payload={
                        "run_id": row["id"],
                        "previous_state": previous_state,
                        "state": next_state,
                        "error_code": error_code,
                    },
                )
                reconciled += 1
        return reconciled

    def get_run(self, run_id: str) -> Run | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return None if row is None else self._run_from_row(row)

    def get_run_by_operation_id(self, operation_id: str) -> Run | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return None if row is None else self._run_from_row(row)

    def update_run_state(
        self,
        *,
        run_id: str,
        state: RunState,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> Run | None:
        timestamp = _utc_now()
        with self._connection() as connection, connection:
            current = connection.execute(
                "SELECT conversation_id, state FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if current is None:
                return None
            connection.execute(
                """
                UPDATE runs
                SET state = ?, error_code = ?, error_detail = ?, updated_at = ?
                WHERE id = ?
                """,
                (state.value, error_code, error_detail, timestamp, run_id),
            )
            self._insert_event(
                connection,
                conversation_id=current["conversation_id"],
                run_id=run_id,
                event_type="run.state_changed",
                payload={
                    "run_id": run_id,
                    "previous_state": current["state"],
                    "state": state.value,
                    "error_code": error_code,
                },
            )
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        assert row is not None
        return self._run_from_row(row)

    def append_event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        conversation_id: str | None = None,
        run_id: str | None = None,
    ) -> StoredEvent:
        with self._connection() as connection, connection:
            sequence = self._insert_event(
                connection,
                conversation_id=conversation_id,
                run_id=run_id,
                event_type=event_type,
                payload=payload,
            )
            row = connection.execute(
                "SELECT * FROM events WHERE sequence = ?", (sequence,)
            ).fetchone()
        assert row is not None
        return self._event_from_row(row)

    def append_tool_completion(
        self,
        *,
        conversation_id: str,
        run_id: str,
        tool_payload: dict[str, Any],
        evidence_payload: dict[str, Any] | None,
    ) -> None:
        """Atomically record one completed tool and its optional evidence."""

        with self._connection() as connection, connection:
            self._insert_event(
                connection,
                conversation_id=conversation_id,
                run_id=run_id,
                event_type="tool.completed",
                payload=tool_payload,
            )
            if evidence_payload is not None:
                self._insert_event(
                    connection,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    event_type="evidence.added",
                    payload=evidence_payload,
                )

    def list_events(
        self,
        *,
        after_sequence: int = 0,
        conversation_id: str | None = None,
        include_global: bool = False,
        limit: int = 250,
    ) -> list[StoredEvent]:
        clauses = ["sequence > ?"]
        parameters: list[Any] = [after_sequence]
        if conversation_id is not None:
            clauses.append(
                "(conversation_id = ? OR conversation_id IS NULL)"
                if include_global
                else "conversation_id = ?"
            )
            parameters.append(conversation_id)
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM events
                WHERE {" AND ".join(clauses)}
                ORDER BY sequence ASC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def list_run_evidence(
        self,
        *,
        conversation_id: str,
        run_id: str,
    ) -> list[StoredEvent]:
        """Return only evidence created by the exact authoritative agent run."""

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM events
                WHERE conversation_id = ?
                  AND run_id = ?
                  AND event_type = 'evidence.added'
                ORDER BY sequence ASC
                """,
                (conversation_id, run_id),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def resolve_conversation_evidence(
        self,
        *,
        conversation_id: str,
        evidence_ids: set[str],
        require_all: bool = True,
    ) -> dict[str, StoredEvent]:
        """Resolve exact durable evidence already bound to one conversation.

        Repeated journal rows are accepted only when their origin and payload are
        identical.  A missing or rebound id makes the durable mission unusable
        instead of silently turning an old citation into a different fact.
        """

        if not evidence_ids:
            return {}
        resolved: dict[str, StoredEvent] = {}
        ordered_ids = sorted(evidence_ids)
        with self._connection() as connection:
            for offset in range(0, len(ordered_ids), 500):
                batch = ordered_ids[offset : offset + 500]
                placeholders = ", ".join("?" for _ in batch)
                rows = connection.execute(
                    f"""
                    SELECT events.* FROM events
                    INNER JOIN runs
                      ON runs.id = events.run_id
                     AND runs.conversation_id = events.conversation_id
                    WHERE events.conversation_id = ?
                      AND events.event_type = 'evidence.added'
                      AND json_extract(events.payload_json, '$.evidenceId')
                          IN ({placeholders})
                    ORDER BY events.sequence ASC
                    """,
                    [conversation_id, *batch],
                ).fetchall()
                for row in rows:
                    event = self._event_from_row(row)
                    evidence_id = event.payload.get("evidenceId")
                    if not isinstance(evidence_id, str) or evidence_id not in evidence_ids:
                        continue
                    if not isinstance(event.run_id, str) or not event.run_id:
                        raise EngineeringMissionConflict(
                            "durable mission evidence has no originating run"
                        )
                    previous = resolved.get(evidence_id)
                    if previous is not None and (
                        previous.run_id != event.run_id
                        or previous.payload != event.payload
                    ):
                        raise EngineeringMissionConflict(
                            "durable mission evidence id was rebound"
                        )
                    resolved[evidence_id] = event
        missing = sorted(evidence_ids.difference(resolved))
        if require_all and missing:
            raise EngineeringMissionConflict(
                "durable mission evidence is missing: " + ", ".join(missing)
            )
        return resolved

    def latest_event_sequence(self) -> int:
        with self._connection() as connection:
            return int(
                connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM events").fetchone()[0]
            )

    def create_change_transaction(
        self,
        *,
        operation_id: str,
        target: ChangeTargetIdentity,
        mutation_scope: str,
        risk_level: ChangeRiskLevel,
        intent: str,
        plan: dict[str, Any],
        plan_digest: str,
        requested_by_actor_id: str,
        approval_expires_at: datetime,
        conversation_id: str | None = None,
        run_id: str | None = None,
        initial_approval_actor_id: str | None = None,
    ) -> tuple[ChangeTransaction, bool]:
        """Create one immutable approval contract, idempotent by operation id."""

        if (
            initial_approval_actor_id is not None
            and initial_approval_actor_id != requested_by_actor_id
        ):
            raise ChangeStoreConflict(
                "initial approval actor must equal the requesting actor"
            )
        transaction_id = str(uuid.uuid4())
        timestamp = _utc_now()
        expires_at = _utc_text(approval_expires_at)
        target_json = _json_dump(target.model_dump(mode="json"))
        initial_state = (
            ChangeTransactionState.APPROVED
            if initial_approval_actor_id is not None
            else ChangeTransactionState.PENDING_APPROVAL
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT * FROM change_transactions WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if existing is not None:
                    if str(existing["plan_digest"]) != plan_digest:
                        raise ChangeStoreConflict(
                            "operation_id is already bound to a different change plan"
                        )
                    transaction = self._change_transaction_from_row(
                        existing,
                        self._approval_row(connection, str(existing["id"])),
                    )
                    connection.rollback()
                    return transaction, False

                live_contract = connection.execute(
                    """
                    SELECT id FROM change_transactions
                    WHERE mutation_scope = ?
                      AND (
                        (state = 'PENDING_APPROVAL' AND approval_expires_at > ?)
                        OR state IN ('APPROVED', 'EXECUTING', 'VERIFYING', 'OBSERVING', 'QUARANTINED')
                      )
                    LIMIT 1
                    """,
                    (mutation_scope, timestamp),
                ).fetchone()
                if live_contract is not None:
                    raise ChangeStoreConflict(
                        "the target already has a live approval contract"
                    )

                if run_id is not None:
                    run = connection.execute(
                        "SELECT conversation_id FROM runs WHERE id = ?",
                        (run_id,),
                    ).fetchone()
                    if run is None:
                        raise ChangeStoreConflict("run_id does not exist")
                    run_conversation_id = str(run["conversation_id"])
                    if (
                        conversation_id is not None
                        and conversation_id != run_conversation_id
                    ):
                        raise ChangeStoreConflict(
                            "run_id belongs to a different conversation"
                        )
                    conversation_id = run_conversation_id
                elif conversation_id is not None:
                    conversation = connection.execute(
                        "SELECT 1 FROM conversations WHERE id = ?",
                        (conversation_id,),
                    ).fetchone()
                    if conversation is None:
                        raise ChangeStoreConflict("conversation_id does not exist")

                connection.execute(
                    """
                    INSERT INTO change_transactions(
                        id, operation_id, conversation_id, run_id, state,
                        target_identity_json, mutation_scope, risk_level, intent,
                        plan_json, plan_digest, requested_by_actor_id,
                        approval_expires_at, result_json, error_code, error_detail,
                        created_at, updated_at, execution_started_at,
                        verification_started_at, completed_at
                    )
                    VALUES (
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, '{}', NULL, NULL, ?, ?, NULL, NULL, NULL
                    )
                    """,
                    (
                        transaction_id,
                        operation_id,
                        conversation_id,
                        run_id,
                        initial_state.value,
                        target_json,
                        mutation_scope,
                        risk_level.value,
                        intent,
                        _json_dump(plan),
                        plan_digest,
                        requested_by_actor_id,
                        expires_at,
                        timestamp,
                        timestamp,
                    ),
                )
                if initial_approval_actor_id is not None:
                    connection.execute(
                        """
                        INSERT INTO change_approvals(
                            transaction_id, decision, plan_digest, actor_id, decided_at
                        )
                        VALUES (?, 'APPROVE', ?, ?, ?)
                        """,
                        (
                            transaction_id,
                            plan_digest,
                            initial_approval_actor_id,
                            timestamp,
                        ),
                    )
                event_payload = {
                    "transaction_id": transaction_id,
                    "operation_id": operation_id,
                    "state": initial_state.value,
                    "mutation_scope": mutation_scope,
                    "risk_level": risk_level.value,
                    "plan_digest": plan_digest,
                    "approval_expires_at": expires_at,
                }
                self._insert_event(
                    connection,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    event_type="change.transaction_created",
                    payload=event_payload,
                )
                row = connection.execute(
                    "SELECT * FROM change_transactions WHERE id = ?",
                    (transaction_id,),
                ).fetchone()
                approval = self._approval_row(connection, transaction_id)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert row is not None
        return self._change_transaction_from_row(row, approval), True

    def get_change_transaction(
        self,
        transaction_id: str,
    ) -> ChangeTransaction | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM change_transactions WHERE id = ?",
                (transaction_id,),
            ).fetchone()
            if row is None:
                return None
            approval_row = self._approval_row(connection, transaction_id)
        return self._change_transaction_from_row(row, approval_row)

    def list_change_transactions(
        self,
        *,
        conversation_id: str | None = None,
        limit: int = 100,
    ) -> list[ChangeTransaction]:
        with self._connection() as connection:
            if conversation_id is None:
                rows = connection.execute(
                    """
                    SELECT * FROM change_transactions
                    ORDER BY created_at DESC, id DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM change_transactions
                    WHERE conversation_id = ?
                    ORDER BY created_at DESC, id DESC LIMIT ?
                    """,
                    (conversation_id, limit),
                ).fetchall()
            values = [
                self._change_transaction_from_row(
                    row,
                    self._approval_row(connection, str(row["id"])),
                )
                for row in rows
            ]
        return values

    def get_mutation_stage_change_for_run(
        self,
        run_id: str,
    ) -> ChangeTransaction | None:
        """Return a durable change whose native lifecycle still owns this run."""

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM change_transactions
                WHERE run_id = ?
                  AND state IN (
                    'APPROVED', 'EXECUTING', 'VERIFYING',
                    'OBSERVING', 'QUARANTINED'
                  )
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            approval = self._approval_row(connection, str(row["id"]))
        return self._change_transaction_from_row(row, approval)

    def live_change_approval_transaction(
        self,
        *,
        mutation_scope: str,
        now: datetime | None = None,
    ) -> ChangeTransaction | None:
        """Return the one unexpired proposal authority for a mutation scope."""

        timestamp = _utc_text(now or datetime.now(timezone.utc))
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM change_transactions
                WHERE mutation_scope = ?
                  AND (
                    (state = 'PENDING_APPROVAL' AND approval_expires_at > ?)
                    OR state IN ('APPROVED', 'EXECUTING', 'VERIFYING', 'OBSERVING', 'QUARANTINED')
                  )
                ORDER BY
                    CASE state
                        WHEN 'QUARANTINED' THEN 0
                        WHEN 'OBSERVING' THEN 1
                        WHEN 'EXECUTING' THEN 2
                        WHEN 'VERIFYING' THEN 2
                        WHEN 'APPROVED' THEN 3
                        ELSE 4
                    END,
                    created_at ASC,
                    id ASC
                LIMIT 1
                """,
                (mutation_scope, timestamp),
            ).fetchone()
            if row is None:
                return None
            return self._change_transaction_from_row(
                row,
                self._approval_row(connection, str(row["id"])),
            )

    def recoverable_change_transaction(self) -> ChangeTransaction | None:
        """Return the durable mutation whose outcome still needs attention.

        A Host restart reconciles EXECUTING and VERIFYING rows to QUARANTINED
        before serving requests. Keeping the pre-reconciliation states in this
        selector also makes status fail closed if it is called during an
        unusual lifecycle boundary. Pending decisions and durable approvals
        remain visible, as does the latest terminal receipt until reviewed. Older
        terminal receipts remain in history, but must not become active again
        when a newer transaction has already been handled.
        """

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM change_transactions
                WHERE state IN (
                    'PENDING_APPROVAL', 'APPROVED',
                    'EXECUTING', 'VERIFYING', 'OBSERVING', 'QUARANTINED'
                )
                   OR (
                       state IN (
                           'COMPLETED', 'ROLLED_BACK', 'FAILED',
                           'REJECTED', 'EXPIRED'
                       )
                       AND receipt_acknowledged_at IS NULL
                       AND id = (
                           SELECT id FROM change_transactions
                           ORDER BY created_at DESC, id DESC
                           LIMIT 1
                       )
                   )
                ORDER BY
                    CASE state
                        WHEN 'QUARANTINED' THEN 0
                        WHEN 'OBSERVING' THEN 1
                        WHEN 'EXECUTING' THEN 2
                        WHEN 'VERIFYING' THEN 2
                        WHEN 'APPROVED' THEN 3
                        WHEN 'PENDING_APPROVAL' THEN 4
                        ELSE 4
                    END,
                    updated_at DESC,
                    id DESC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            approval_row = self._approval_row(connection, str(row["id"]))
        return self._change_transaction_from_row(row, approval_row)

    def acknowledge_change_receipt(
        self,
        *,
        transaction_id: str,
        actor_id: str,
        acknowledged_at: datetime | None = None,
    ) -> tuple[ChangeTransaction | None, bool]:
        """Persist that a user has reviewed a terminal change receipt."""

        if not actor_id.strip():
            raise ValueError("receipt acknowledgement requires an actor")
        terminal_states = {
            ChangeTransactionState.COMPLETED.value,
            ChangeTransactionState.ROLLED_BACK.value,
            ChangeTransactionState.FAILED.value,
            ChangeTransactionState.REJECTED.value,
            ChangeTransactionState.EXPIRED.value,
        }
        timestamp = _utc_text(acknowledged_at or datetime.now(timezone.utc))
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM change_transactions WHERE id = ?",
                    (transaction_id,),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return None, False
                if str(row["state"]) not in terminal_states:
                    raise ChangeStoreConflict(
                        "only a terminal change receipt can be acknowledged"
                    )
                recorded = row["receipt_acknowledged_at"] is None
                if recorded:
                    connection.execute(
                        """
                        UPDATE change_transactions
                        SET receipt_acknowledged_at = ?,
                            receipt_acknowledged_by = ?,
                            updated_at = ?
                        WHERE id = ? AND receipt_acknowledged_at IS NULL
                        """,
                        (timestamp, actor_id, timestamp, transaction_id),
                    )
                    self._insert_event(
                        connection,
                        conversation_id=row["conversation_id"],
                        run_id=row["run_id"],
                        event_type="change.receipt_acknowledged",
                        payload={
                            "transaction_id": transaction_id,
                            "state": str(row["state"]),
                            "actor_id": actor_id,
                        },
                    )
                updated = connection.execute(
                    "SELECT * FROM change_transactions WHERE id = ?",
                    (transaction_id,),
                ).fetchone()
                approval = self._approval_row(connection, transaction_id)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert updated is not None
        return self._change_transaction_from_row(updated, approval), recorded

    def decide_change_transaction(
        self,
        *,
        transaction_id: str,
        decision: ApprovalDecision,
        expected_plan_digest: str,
        actor_id: str,
        decided_at: datetime | None = None,
    ) -> tuple[ChangeTransaction | None, bool]:
        """Record the one-time decision and atomically advance the contract."""

        decision_time = decided_at or datetime.now(timezone.utc)
        decided_at_text = _utc_text(decision_time)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM change_transactions WHERE id = ?",
                    (transaction_id,),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return None, False
                if str(row["plan_digest"]) != expected_plan_digest:
                    raise ChangeStoreConflict("plan digest does not match the approval contract")

                existing_approval = self._approval_row(connection, transaction_id)
                if existing_approval is not None:
                    existing = self._approval_from_row(existing_approval)
                    if (
                        existing.decision is decision
                        and existing.actor_id == actor_id
                        and existing.plan_digest == expected_plan_digest
                    ):
                        transaction = self._change_transaction_from_row(
                            row,
                            existing_approval,
                        )
                        connection.rollback()
                        return transaction, False
                    raise ChangeStoreConflict(
                        "the approval contract already has a one-time decision"
                    )

                state = ChangeTransactionState(str(row["state"]))
                if state is not ChangeTransactionState.PENDING_APPROVAL:
                    raise ChangeStoreConflict(
                        f"cannot decide a change transaction in state {state.value}"
                    )

                expires_at = datetime.fromisoformat(str(row["approval_expires_at"]))
                if decision_time >= expires_at:
                    connection.execute(
                        """
                        UPDATE change_transactions
                        SET state = 'EXPIRED', updated_at = ?, completed_at = ?
                        WHERE id = ?
                        """,
                        (decided_at_text, decided_at_text, transaction_id),
                    )
                    self._insert_event(
                        connection,
                        conversation_id=row["conversation_id"],
                        run_id=row["run_id"],
                        event_type="approval.expired",
                        payload={
                            "transaction_id": transaction_id,
                            "plan_digest": expected_plan_digest,
                            "state": ChangeTransactionState.EXPIRED.value,
                        },
                    )
                    updated = connection.execute(
                        "SELECT * FROM change_transactions WHERE id = ?",
                        (transaction_id,),
                    ).fetchone()
                    connection.commit()
                    assert updated is not None
                    return self._change_transaction_from_row(updated, None), True

                next_state = (
                    ChangeTransactionState.APPROVED
                    if decision is ApprovalDecision.APPROVE
                    else ChangeTransactionState.REJECTED
                )
                connection.execute(
                    """
                    INSERT INTO change_approvals(
                        transaction_id, decision, plan_digest, actor_id, decided_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        transaction_id,
                        decision.value,
                        expected_plan_digest,
                        actor_id,
                        decided_at_text,
                    ),
                )
                connection.execute(
                    """
                    UPDATE change_transactions
                    SET state = ?, updated_at = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (
                        next_state.value,
                        decided_at_text,
                        decided_at_text
                        if next_state is ChangeTransactionState.REJECTED
                        else None,
                        transaction_id,
                    ),
                )
                self._insert_event(
                    connection,
                    conversation_id=row["conversation_id"],
                    run_id=row["run_id"],
                    event_type="approval.decided",
                    payload={
                        "transaction_id": transaction_id,
                        "decision": decision.value,
                        "actor_id": actor_id,
                        "plan_digest": expected_plan_digest,
                        "previous_state": state.value,
                        "state": next_state.value,
                    },
                )
                updated = connection.execute(
                    "SELECT * FROM change_transactions WHERE id = ?",
                    (transaction_id,),
                ).fetchone()
                approval_row = self._approval_row(connection, transaction_id)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert updated is not None and approval_row is not None
        return self._change_transaction_from_row(updated, approval_row), True

    def begin_change_execution(
        self,
        *,
        transaction_id: str,
        expected_plan_digest: str,
        execution_target: ChangeTargetIdentity | None = None,
        started_at: datetime | None = None,
    ) -> ChangeTransaction | None:
        """Acquire the durable target lease and enter EXECUTING atomically."""

        execution_time = started_at or datetime.now(timezone.utc)
        timestamp = _utc_text(execution_time)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM change_transactions WHERE id = ?",
                    (transaction_id,),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return None
                if str(row["plan_digest"]) != expected_plan_digest:
                    raise ChangeStoreConflict("plan digest does not match the approved contract")
                approved_target = ChangeTargetIdentity.model_validate(
                    _json_load(row["target_identity_json"])
                )
                bound_target = execution_target or approved_target
                if not approved_target.same_artifact(bound_target):
                    raise ChangeStoreConflict("execution target differs from the approved artifact")
                state = ChangeTransactionState(str(row["state"]))
                if state is not ChangeTransactionState.APPROVED:
                    raise ChangeStoreConflict(
                        f"cannot execute a change transaction in state {state.value}"
                    )
                approval = self._approval_row(connection, transaction_id)
                if approval is None or str(approval["decision"]) != ApprovalDecision.APPROVE.value:
                    raise ChangeStoreConflict("transaction has no durable approval")
                if str(approval["plan_digest"]) != expected_plan_digest:
                    raise ChangeStoreConflict("approval digest no longer matches the plan")

                result = _json_load(str(row["result_json"]))
                preflight = result.get("preflight") if isinstance(result, dict) else None
                if _is_current_approved_preflight_failure(preflight):
                    preflight["current"] = False
                    preflight["superseded_at"] = timestamp

                expires_at = datetime.fromisoformat(str(row["approval_expires_at"]))
                if execution_time >= expires_at:
                    connection.execute(
                        """
                        UPDATE change_transactions
                        SET state = 'EXPIRED', error_code = 'approval_expired',
                            error_detail = ?, updated_at = ?, completed_at = ?
                        WHERE id = ?
                        """,
                        (
                            "Approval expired before native execution began.",
                            timestamp,
                            timestamp,
                            transaction_id,
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE mutation_leases
                        SET released_at = ?, release_reason = ?
                        WHERE transaction_id = ? AND released_at IS NULL
                        """,
                        (
                            timestamp,
                            "approval_expired_before_execution",
                            transaction_id,
                        ),
                    )
                    self._insert_event(
                        connection,
                        conversation_id=row["conversation_id"],
                        run_id=row["run_id"],
                        event_type="approval.expired",
                        payload={
                            "transaction_id": transaction_id,
                            "plan_digest": expected_plan_digest,
                            "previous_state": state.value,
                            "state": ChangeTransactionState.EXPIRED.value,
                            "reason": "expired_before_execution",
                        },
                    )
                    connection.commit()
                    raise ApprovalExpiredConflict(
                        "approval expired before change execution began"
                    )

                mutation_scope = str(row["mutation_scope"])
                try:
                    connection.execute(
                        """
                        INSERT INTO mutation_leases(
                            id, mutation_scope, transaction_id, acquired_at,
                            released_at, release_reason
                        )
                        VALUES (?, ?, ?, ?, NULL, NULL)
                        """,
                        (str(uuid.uuid4()), mutation_scope, transaction_id, timestamp),
                    )
                except sqlite3.IntegrityError as exc:
                    holder = connection.execute(
                        """
                        SELECT transaction_id FROM mutation_leases
                        WHERE mutation_scope = ? AND released_at IS NULL
                        """,
                        (mutation_scope,),
                    ).fetchone()
                    if holder is not None:
                        raise MutationLeaseConflict(
                            mutation_scope,
                            str(holder["transaction_id"]),
                        ) from exc
                    raise

                connection.execute(
                    """
                    UPDATE change_transactions
                    SET state = 'EXECUTING', updated_at = ?, execution_started_at = ?,
                        execution_target_json = ?, result_json = ?
                    WHERE id = ?
                    """,
                    (
                        timestamp,
                        timestamp,
                        _json_dump(bound_target.model_dump(mode="json")),
                        _json_dump(result),
                        transaction_id,
                    ),
                )
                self._insert_event(
                    connection,
                    conversation_id=row["conversation_id"],
                    run_id=row["run_id"],
                    event_type="change.execution_started",
                    payload={
                        "transaction_id": transaction_id,
                        "previous_state": state.value,
                        "state": ChangeTransactionState.EXECUTING.value,
                        "mutation_scope": mutation_scope,
                        "plan_digest": expected_plan_digest,
                    },
                )
                updated = connection.execute(
                    "SELECT * FROM change_transactions WHERE id = ?",
                    (transaction_id,),
                ).fetchone()
                connection.commit()
            except ApprovalExpiredConflict:
                raise
            except BaseException:
                connection.rollback()
                raise
        assert updated is not None
        return self._change_transaction_from_row(updated, approval)

    def record_approved_change_preflight_failure(
        self,
        *,
        transaction_id: str,
        expected_plan_digest: str,
        reason_code: str,
        stage: str,
    ) -> tuple[ChangeTransaction | None, bool]:
        """Persist a bounded preparation failure without consuming approval."""

        if (
            not isinstance(reason_code, str)
            or _CHANGE_PREFLIGHT_REASON_CODE.fullmatch(reason_code) is None
            or not isinstance(stage, str)
            or _CHANGE_PREFLIGHT_STAGE.fullmatch(stage) is None
        ):
            raise ChangeStoreConflict("change preflight diagnostic is invalid")

        timestamp = _utc_now()
        record: ApprovedChangePreflightFailure = {
            "status": "failed_before_execution",
            "current": True,
            "native_execution_started": False,
            "mutation_attempted": False,
            "reason_code": reason_code,
            "stage": stage,
            "recorded_at": timestamp,
        }
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM change_transactions WHERE id = ?",
                    (transaction_id,),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return None, False
                if str(row["plan_digest"]) != expected_plan_digest:
                    raise ChangeStoreConflict(
                        "plan digest does not match the approved contract"
                    )
                state = ChangeTransactionState(str(row["state"]))
                if (
                    state is not ChangeTransactionState.APPROVED
                    or row["execution_started_at"] is not None
                    or row["execution_target_json"] is not None
                ):
                    raise ChangeStoreConflict(
                        "preflight diagnostics require an approved change before execution"
                    )
                approval = self._approval_row(connection, transaction_id)
                if (
                    approval is None
                    or str(approval["decision"]) != ApprovalDecision.APPROVE.value
                    or str(approval["plan_digest"]) != expected_plan_digest
                ):
                    raise ChangeStoreConflict(
                        "transaction has no matching durable approval"
                    )
                mutation_lease = connection.execute(
                    """
                    SELECT 1 FROM mutation_leases
                    WHERE transaction_id = ? AND released_at IS NULL
                    LIMIT 1
                    """,
                    (transaction_id,),
                ).fetchone()
                if mutation_lease is not None:
                    raise ChangeStoreConflict(
                        "cannot record preflight diagnostics during native execution"
                    )

                result = _json_load(str(row["result_json"]))
                if not isinstance(result, dict):
                    raise ChangeStoreConflict("stored change result is invalid")
                previous = result.get("preflight")
                if isinstance(previous, dict) and previous.get("current") is True:
                    if not _is_current_approved_preflight_failure(previous):
                        raise ChangeStoreConflict(
                            "stored current preflight diagnostic is invalid"
                        )
                    previous["current"] = False
                    previous["superseded_at"] = timestamp
                result["preflight"] = record
                connection.execute(
                    """
                    UPDATE change_transactions
                    SET result_json = ?, updated_at = ?
                    WHERE id = ? AND state = 'APPROVED'
                    """,
                    (_json_dump(result), timestamp, transaction_id),
                )
                updated = connection.execute(
                    "SELECT * FROM change_transactions WHERE id = ?",
                    (transaction_id,),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert updated is not None
        return self._change_transaction_from_row(updated, approval), True

    def fail_approved_change_preflight(
        self,
        *,
        transaction_id: str,
        expected_plan_digest: str,
    ) -> tuple[ChangeTransaction | None, bool]:
        """Terminalize a direct approval that failed before native execution."""

        timestamp = _utc_now()
        error_code = "change_preflight_failed_before_mutation"
        error_detail = (
            "Native change preflight failed before execution began; "
            "no mutation was attempted."
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM change_transactions WHERE id = ?",
                    (transaction_id,),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return None, False
                if str(row["plan_digest"]) != expected_plan_digest:
                    raise ChangeStoreConflict(
                        "plan digest does not match the approved contract"
                    )
                state = ChangeTransactionState(str(row["state"]))
                approval = self._approval_row(connection, transaction_id)
                if (
                    state is ChangeTransactionState.FAILED
                    and str(row["error_code"] or "") == error_code
                ):
                    transaction = self._change_transaction_from_row(row, approval)
                    connection.rollback()
                    return transaction, False
                if state is not ChangeTransactionState.APPROVED:
                    raise ChangeStoreConflict(
                        "only an approved, not-yet-started change can fail preflight"
                    )
                if (
                    approval is None
                    or str(approval["decision"]) != ApprovalDecision.APPROVE.value
                    or str(approval["plan_digest"]) != expected_plan_digest
                ):
                    raise ChangeStoreConflict(
                        "transaction has no matching durable approval"
                    )

                plan = _json_load(str(row["plan_json"]))
                validation = plan.get("validation")
                not_executed = (
                    list(validation)
                    if isinstance(validation, list)
                    and all(isinstance(item, str) for item in validation)
                    else []
                )
                result = _json_load(str(row["result_json"]))
                result["preflight"] = {
                    "status": "failed_before_execution",
                    "native_execution_started": False,
                    "mutation_attempted": False,
                    "executed_checks": [],
                    "not_executed": not_executed,
                }
                connection.execute(
                    """
                    UPDATE change_transactions
                    SET state = 'FAILED', result_json = ?, error_code = ?,
                        error_detail = ?, updated_at = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (
                        _json_dump(result),
                        error_code,
                        error_detail,
                        timestamp,
                        timestamp,
                        transaction_id,
                    ),
                )
                self._insert_event(
                    connection,
                    conversation_id=row["conversation_id"],
                    run_id=row["run_id"],
                    event_type="change.execution_finished",
                    payload={
                        "transaction_id": transaction_id,
                        "previous_state": state.value,
                        "state": ChangeTransactionState.FAILED.value,
                        "error_code": error_code,
                        "lease_released": True,
                        "native_execution_started": False,
                        "mutation_attempted": False,
                    },
                )
                updated = connection.execute(
                    "SELECT * FROM change_transactions WHERE id = ?",
                    (transaction_id,),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert updated is not None
        return self._change_transaction_from_row(updated, approval), True

    def mark_change_verifying(
        self,
        *,
        transaction_id: str,
        apply_evidence: dict[str, Any],
    ) -> ChangeTransaction | None:
        timestamp = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM change_transactions WHERE id = ?",
                    (transaction_id,),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return None
                state = ChangeTransactionState(str(row["state"]))
                if state is not ChangeTransactionState.EXECUTING:
                    raise ChangeStoreConflict(
                        f"cannot verify a change transaction in state {state.value}"
                    )
                result = _json_load(str(row["result_json"]))
                result["apply"] = apply_evidence
                connection.execute(
                    """
                    UPDATE change_transactions
                    SET state = 'VERIFYING', result_json = ?, updated_at = ?,
                        verification_started_at = ?
                    WHERE id = ?
                    """,
                    (_json_dump(result), timestamp, timestamp, transaction_id),
                )
                self._insert_event(
                    connection,
                    conversation_id=row["conversation_id"],
                    run_id=row["run_id"],
                    event_type="change.verification_started",
                    payload={
                        "transaction_id": transaction_id,
                        "previous_state": state.value,
                        "state": ChangeTransactionState.VERIFYING.value,
                    },
                )
                updated = connection.execute(
                    "SELECT * FROM change_transactions WHERE id = ?",
                    (transaction_id,),
                ).fetchone()
                approval = self._approval_row(connection, transaction_id)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert updated is not None
        return self._change_transaction_from_row(updated, approval)

    def record_native_change_checkpoint(
        self,
        transaction_id: str,
        checkpoint: dict[str, Any],
    ) -> ChangeTransaction | None:
        """Persist monotonic native recovery identity before UI mutation."""

        stage = str(checkpoint.get("stage") or "")
        if stage not in _NATIVE_CHECKPOINT_ORDER:
            raise ChangeStoreConflict(f"unsupported native checkpoint stage: {stage}")
        if checkpoint.get("host_transaction_id") != transaction_id:
            raise ChangeStoreConflict(
                "native checkpoint host transaction identity does not match"
            )
        timestamp = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM change_transactions WHERE id = ?",
                    (transaction_id,),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return None
                state = ChangeTransactionState(str(row["state"]))
                if state is not ChangeTransactionState.EXECUTING:
                    raise ChangeStoreConflict(
                        f"cannot checkpoint a change transaction in state {state.value}"
                    )
                if checkpoint.get("host_plan_digest") != row["plan_digest"]:
                    raise ChangeStoreConflict(
                        "native checkpoint plan digest does not match"
                    )
                result = _json_load(str(row["result_json"]))
                existing = result.get("native_checkpoint")
                if existing is not None and not isinstance(existing, dict):
                    raise ChangeStoreConflict(
                        "stored native checkpoint has an invalid shape"
                    )
                if isinstance(existing, dict):
                    previous_stage = str(existing.get("stage") or "")
                    if (
                        previous_stage not in _NATIVE_CHECKPOINT_ORDER
                        or _NATIVE_CHECKPOINT_ORDER[stage]
                        < _NATIVE_CHECKPOINT_ORDER[previous_stage]
                    ):
                        raise ChangeStoreConflict(
                            "native checkpoint stage cannot move backwards"
                        )
                    for field in _NATIVE_CHECKPOINT_IMMUTABLE_FIELDS:
                        if (
                            field in existing
                            and field in checkpoint
                            and existing[field] != checkpoint[field]
                        ):
                            raise ChangeStoreConflict(
                                f"native checkpoint changed immutable field {field}"
                            )
                merged = {**(existing or {}), **checkpoint}
                result["native_checkpoint"] = merged
                connection.execute(
                    """
                    UPDATE change_transactions
                    SET result_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (_json_dump(result), timestamp, transaction_id),
                )
                self._insert_event(
                    connection,
                    conversation_id=row["conversation_id"],
                    run_id=row["run_id"],
                    event_type="change.native_checkpoint",
                    payload={
                        "transaction_id": transaction_id,
                        "stage": stage,
                        "mcp_transaction_id": merged.get("mcp_transaction_id"),
                        "mcp_plan_id": merged.get("mcp_plan_id"),
                    },
                )
                updated = connection.execute(
                    "SELECT * FROM change_transactions WHERE id = ?",
                    (transaction_id,),
                ).fetchone()
                approval = self._approval_row(connection, transaction_id)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert updated is not None
        return self._change_transaction_from_row(updated, approval)

    def finish_change_execution(
        self,
        *,
        transaction_id: str,
        state: ChangeTransactionState,
        verification_evidence: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> ChangeTransaction | None:
        allowed_final_states = {
            ChangeTransactionState.OBSERVING,
            ChangeTransactionState.COMPLETED,
            ChangeTransactionState.ROLLED_BACK,
            ChangeTransactionState.FAILED,
            ChangeTransactionState.QUARANTINED,
        }
        if state not in allowed_final_states:
            raise ValueError("execution outcome must be a supported change state")
        timestamp = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM change_transactions WHERE id = ?",
                    (transaction_id,),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return None
                previous_state = ChangeTransactionState(str(row["state"]))
                if previous_state not in {
                    ChangeTransactionState.EXECUTING,
                    ChangeTransactionState.VERIFYING,
                }:
                    raise ChangeStoreConflict(
                        f"cannot finish a change transaction in state {previous_state.value}"
                    )
                if (
                    state is ChangeTransactionState.COMPLETED
                    and previous_state is not ChangeTransactionState.VERIFYING
                ):
                    raise ChangeStoreConflict(
                        "a change cannot complete before verification begins"
                    )

                result = _json_load(str(row["result_json"]))
                if verification_evidence is not None:
                    result["verification"] = verification_evidence
                connection.execute(
                    """
                    UPDATE change_transactions
                    SET state = ?, result_json = ?, error_code = ?, error_detail = ?,
                        updated_at = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (
                        state.value,
                        _json_dump(result),
                        error_code,
                        error_detail,
                        timestamp,
                        None
                        if state
                        in {
                            ChangeTransactionState.OBSERVING,
                            ChangeTransactionState.QUARANTINED,
                        }
                        else timestamp,
                        transaction_id,
                    ),
                )
                if state not in {
                    ChangeTransactionState.OBSERVING,
                    ChangeTransactionState.QUARANTINED,
                }:
                    connection.execute(
                        """
                        UPDATE mutation_leases
                        SET released_at = ?, release_reason = ?
                        WHERE transaction_id = ? AND released_at IS NULL
                        """,
                        (timestamp, state.value, transaction_id),
                    )
                    connection.execute(
                        """
                        UPDATE target_operation_leases
                        SET state = 'RELEASED', released_at = ?, release_reason = ?
                        WHERE operation_id = ? AND released_at IS NULL
                        """,
                        (timestamp, state.value, transaction_id),
                    )
                elif state is ChangeTransactionState.QUARANTINED:
                    connection.execute(
                        """
                        UPDATE target_operation_leases
                        SET state = 'STALE_BLOCKED', heartbeat_at = ?,
                            expires_at = ?, release_reason = ?
                        WHERE operation_id = ? AND released_at IS NULL
                        """,
                        (
                            timestamp,
                            timestamp,
                            "change_outcome_uncertain",
                            transaction_id,
                        ),
                    )
                self._insert_event(
                    connection,
                    conversation_id=row["conversation_id"],
                    run_id=row["run_id"],
                    event_type="change.execution_finished",
                    payload={
                        "transaction_id": transaction_id,
                        "previous_state": previous_state.value,
                        "state": state.value,
                        "error_code": error_code,
                        "lease_released": state
                        not in {
                            ChangeTransactionState.OBSERVING,
                            ChangeTransactionState.QUARANTINED,
                        },
                    },
                )
                updated = connection.execute(
                    "SELECT * FROM change_transactions WHERE id = ?",
                    (transaction_id,),
                ).fetchone()
                approval = self._approval_row(connection, transaction_id)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert updated is not None
        return self._change_transaction_from_row(updated, approval)

    def expire_change_approvals(
        self,
        *,
        conversation_id: str,
        now: datetime | None = None,
    ) -> int:
        """Advance elapsed, unexecuted approvals before resuming a conversation."""
        timestamp = _utc_text(now or datetime.now(timezone.utc))
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    """
                    SELECT id, conversation_id, run_id, state FROM change_transactions
                    WHERE conversation_id = ?
                      AND state IN ('PENDING_APPROVAL', 'APPROVED')
                      AND julianday(approval_expires_at) <= julianday(?)
                      AND execution_started_at IS NULL
                    """,
                    (conversation_id, timestamp),
                ).fetchall()
                for row in rows:
                    connection.execute(
                        """
                        UPDATE change_transactions
                        SET state = 'EXPIRED', error_code = 'approval_expired',
                            error_detail = 'Approval expired before execution.',
                            updated_at = ?, completed_at = ?
                        WHERE id = ?
                        """,
                        (timestamp, timestamp, row["id"]),
                    )
                    self._insert_event(
                        connection,
                        conversation_id=row["conversation_id"],
                        run_id=row["run_id"],
                        event_type="approval.expired",
                        payload={
                            "transaction_id": row["id"],
                            "previous_state": row["state"],
                            "state": "EXPIRED",
                        },
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return len(rows)

    def reconcile_interrupted_change_transactions(
        self,
        *,
        now: datetime | None = None,
    ) -> int:
        """Retain proven positive decisions; quarantine interrupted mutations."""

        pass

        timestamp = _utc_text(now or datetime.now(timezone.utc))
        reconciled = 0
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    """
                    SELECT * FROM change_transactions
                    WHERE state IN (
                        'PENDING_APPROVAL', 'APPROVED', 'EXECUTING', 'VERIFYING', 'OBSERVING'
                    )
                    """
                ).fetchall()
                for row in rows:
                    previous_state = ChangeTransactionState(str(row["state"]))
                    next_state: ChangeTransactionState | None = None
                    error_code: str | None = None
                    if (
                        previous_state in {
                            ChangeTransactionState.PENDING_APPROVAL,
                            ChangeTransactionState.APPROVED,
                        }
                        and str(row["approval_expires_at"]) <= timestamp
                    ):
                        next_state = ChangeTransactionState.EXPIRED
                        error_code = "approval_expired"
                    elif previous_state in {
                        ChangeTransactionState.EXECUTING,
                        ChangeTransactionState.VERIFYING,
                        ChangeTransactionState.OBSERVING,
                    }:
                        next_state = ChangeTransactionState.QUARANTINED
                        error_code = (
                            "interrupted_simulation_decision_requires_recovery"
                            if previous_state is ChangeTransactionState.OBSERVING
                            else "interrupted_change_outcome_uncertain"
                        )
                    if next_state is None:
                        continue
                    connection.execute(
                        """
                        UPDATE change_transactions
                        SET state = ?, error_code = ?, error_detail = ?,
                            updated_at = ?, completed_at = ?
                        WHERE id = ?
                        """,
                        (
                            next_state.value,
                            error_code,
                            (
                                "Approval expired while the Host was not running."
                                if next_state is ChangeTransactionState.EXPIRED
                                else (
                                    "Host restarted after mutation execution began; "
                                    "native state must be reconciled before release."
                                )
                            ),
                            timestamp,
                            timestamp
                            if next_state is ChangeTransactionState.EXPIRED
                            else None,
                            row["id"],
                        ),
                    )
                    self._insert_event(
                        connection,
                        conversation_id=row["conversation_id"],
                        run_id=row["run_id"],
                        event_type="change.reconciled",
                        payload={
                            "transaction_id": row["id"],
                            "previous_state": previous_state.value,
                            "state": next_state.value,
                            "error_code": error_code,
                            "lease_released": False,
                        },
                    )
                    reconciled += 1
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return reconciled


    def finalize_observing_change(
        self,
        *,
        transaction_id: str,
        expected_plan_digest: str,
        expected_simulation_receipt_digest: str,
        state: ChangeTransactionState,
        evidence: dict[str, Any],
        actor_id: str,
        decision: str,
    ) -> ChangeTransaction | None:
        if state not in {ChangeTransactionState.COMPLETED, ChangeTransactionState.ROLLED_BACK}:
            raise ValueError("observing change finalization must be completed or rolled back")
        if (decision == "accept") != (state is ChangeTransactionState.COMPLETED):
            raise ValueError("simulation finalization decision/state mismatch")
        timestamp = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM change_transactions WHERE id = ?", (transaction_id,)
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return None
                if ChangeTransactionState(str(row["state"])) is not ChangeTransactionState.OBSERVING:
                    raise ChangeStoreConflict("change transaction is not awaiting a simulation decision")
                if str(row["plan_digest"]) != expected_plan_digest:
                    raise ChangeStoreConflict("simulation decision plan digest differs")
                result = _json_load(str(row["result_json"]))
                receipt = (result.get("verification") or {}).get("simulation_receipt")
                if not isinstance(receipt, dict) or receipt.get("receipt_digest") != expected_simulation_receipt_digest:
                    raise ChangeStoreConflict("simulation decision receipt digest differs")
                result["simulation_finalization"] = {
                    "actor_id": actor_id,
                    "decision": decision,
                    "expected_plan_digest": expected_plan_digest,
                    "expected_simulation_receipt_digest": expected_simulation_receipt_digest,
                    **evidence,
                }
                connection.execute(
                    """
                    UPDATE change_transactions
                    SET state = ?, result_json = ?, updated_at = ?, completed_at = ?
                    WHERE id = ? AND state = 'OBSERVING'
                    """,
                    (state.value, _json_dump(result), timestamp, timestamp, transaction_id),
                )
                connection.execute(
                    "UPDATE mutation_leases SET released_at = ?, release_reason = ? WHERE transaction_id = ? AND released_at IS NULL",
                    (timestamp, state.value, transaction_id),
                )
                connection.execute(
                    "UPDATE target_operation_leases SET state = 'RELEASED', released_at = ?, release_reason = ? WHERE operation_id = ? AND released_at IS NULL",
                    (timestamp, state.value, transaction_id),
                )
                self._insert_event(connection, conversation_id=row["conversation_id"], run_id=row["run_id"], event_type="change.simulation_finalized", payload={"transaction_id": transaction_id, "state": state.value, "decision": decision, "actor_id": actor_id})
                updated = connection.execute("SELECT * FROM change_transactions WHERE id = ?", (transaction_id,)).fetchone()
                approval = self._approval_row(connection, transaction_id)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert updated is not None
        return self._change_transaction_from_row(updated, approval)

    def resolve_quarantined_change(
        self,
        *,
        transaction_id: str,
        state: ChangeTransactionState,
        evidence: dict[str, Any],
        actor_id: str,
        error_code: str | None = None,
        error_detail: str | None = None,
        held_target_lease_token: str | None = None,
    ) -> ChangeTransaction | None:
        allowed = {
            ChangeTransactionState.COMPLETED,
            ChangeTransactionState.ROLLED_BACK,
            ChangeTransactionState.QUARANTINED,
        }
        if state not in allowed:
            raise ValueError("quarantine resolution has an unsupported state")
        if held_target_lease_token is not None and state is not ChangeTransactionState.QUARANTINED:
            raise ValueError("only an in-progress quarantine record can retain its target lease")
        timestamp = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM change_transactions WHERE id = ?",
                    (transaction_id,),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return None
                if str(row["state"]) != ChangeTransactionState.QUARANTINED.value:
                    raise ChangeStoreConflict(
                        "only a quarantined change can be reconciled"
                    )
                if held_target_lease_token is not None:
                    held_lease = connection.execute(
                        """
                        SELECT 1 FROM target_operation_leases
                        WHERE operation_id = ? AND owner_instance_id = ?
                          AND holder_token = ? AND state = 'ACTIVE'
                          AND released_at IS NULL
                        """,
                        (transaction_id, self.instance_id, held_target_lease_token),
                    ).fetchone()
                    if held_lease is None:
                        raise ChangeStoreConflict("target-operation lease ownership was lost")
                result = _json_load(str(row["result_json"]))
                result["reconciliation"] = evidence
                connection.execute(
                    """
                    UPDATE change_transactions
                    SET state = ?, result_json = ?, error_code = ?, error_detail = ?,
                        updated_at = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (
                        state.value,
                        _json_dump(result),
                        error_code,
                        error_detail,
                        timestamp,
                        None
                        if state is ChangeTransactionState.QUARANTINED
                        else timestamp,
                        transaction_id,
                    ),
                )
                if state is not ChangeTransactionState.QUARANTINED:
                    connection.execute(
                        """
                        UPDATE mutation_leases
                        SET released_at = ?, release_reason = ?
                        WHERE transaction_id = ? AND released_at IS NULL
                        """,
                        (timestamp, f"RECONCILED_{state.value}", transaction_id),
                    )
                    connection.execute(
                        """
                        UPDATE target_operation_leases
                        SET state = 'RELEASED', released_at = ?, release_reason = ?
                        WHERE operation_id = ? AND released_at IS NULL
                        """,
                        (
                            timestamp,
                            f"RECONCILED_{state.value}",
                            transaction_id,
                        ),
                    )
                elif held_target_lease_token is None:
                    connection.execute(
                        """
                        UPDATE target_operation_leases
                        SET state = 'STALE_BLOCKED', heartbeat_at = ?,
                            expires_at = ?, release_reason = ?
                        WHERE operation_id = ? AND released_at IS NULL
                        """,
                        (
                            timestamp,
                            timestamp,
                            "reconciliation_still_uncertain",
                            transaction_id,
                        ),
                    )
                self._insert_event(
                    connection,
                    conversation_id=row["conversation_id"],
                    run_id=row["run_id"],
                    event_type="change.quarantine_reconciled",
                    payload={
                        "transaction_id": transaction_id,
                        "previous_state": ChangeTransactionState.QUARANTINED.value,
                        "state": state.value,
                        "lease_released": state
                        is not ChangeTransactionState.QUARANTINED,
                        "error_code": error_code,
                        "actor_id": actor_id,
                    },
                )
                updated = connection.execute(
                    "SELECT * FROM change_transactions WHERE id = ?",
                    (transaction_id,),
                ).fetchone()
                approval = self._approval_row(connection, transaction_id)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert updated is not None
        return self._change_transaction_from_row(updated, approval)

    def acquire_target_operation_lease(
        self,
        *,
        operation_id: str,
        owner_kind: str,
        holder_token: str,
        target_identity: dict[str, Any],
        allowed_change_transaction_id: str | None = None,
        ttl_seconds: int = 900,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Atomically reserve the one UI/target-changing Host operation.

        Expiry never hands the target to an unrelated operation. A parked or
        expired lease may only be reclaimed by the exact same operation and
        target identity for explicit recovery.
        """

        current = now or datetime.now(timezone.utc)
        timestamp = _utc_text(current)
        expires_at = _utc_text(current + timedelta(seconds=ttl_seconds))
        target_json = _json_dump(target_identity)
        scope_key = "xg5000-host-target"
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                recovery_authority = self._selected_target_recovery_authority_row(
                    connection
                )
                if recovery_authority is not None and (
                    str(recovery_authority["operation_id"]) != operation_id
                    or str(recovery_authority["owner_kind"]) != owner_kind
                ):
                    raise TargetOperationLeaseConflict(
                        operation_id=str(recovery_authority["operation_id"]),
                        owner_kind=str(recovery_authority["owner_kind"]),
                        stale=str(recovery_authority.get("lease_state") or "")
                        != "ACTIVE",
                    )

                durable = connection.execute(
                    """
                    SELECT transaction_id FROM mutation_leases
                    WHERE released_at IS NULL
                    ORDER BY acquired_at LIMIT 1
                    """
                ).fetchone()
                if (
                    durable is not None
                    and str(durable["transaction_id"])
                    != allowed_change_transaction_id
                ):
                    raise TargetOperationLeaseConflict(
                        operation_id=str(durable["transaction_id"]),
                        owner_kind="offline_change",
                        stale=False,
                    )

                existing = connection.execute(
                    """
                    SELECT * FROM target_operation_leases
                    WHERE scope_key = ? AND released_at IS NULL
                    """,
                    (scope_key,),
                ).fetchone()
                if existing is None:
                    lease_id = str(uuid.uuid4())
                    connection.execute(
                        """
                        INSERT INTO target_operation_leases(
                            id, scope_key, operation_id, owner_kind,
                            owner_instance_id, holder_token,
                            target_identity_json, state, acquired_at,
                            heartbeat_at, expires_at, released_at, release_reason
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, NULL, NULL)
                        """,
                        (
                            lease_id,
                            scope_key,
                            operation_id,
                            owner_kind,
                            self.instance_id,
                            holder_token,
                            target_json,
                            timestamp,
                            timestamp,
                            expires_at,
                        ),
                    )
                else:
                    exact_operation = (
                        str(existing["operation_id"]) == operation_id
                        and str(existing["owner_kind"]) == owner_kind
                        and str(existing["target_identity_json"]) == target_json
                    )
                    same_holder = (
                        str(existing["owner_instance_id"]) == self.instance_id
                        and str(existing["holder_token"]) == holder_token
                    )
                    expired = str(existing["expires_at"]) <= timestamp
                    parked = str(existing["state"]) == "STALE_BLOCKED"
                    if exact_operation and (same_holder or parked or expired):
                        connection.execute(
                            """
                            UPDATE target_operation_leases
                            SET owner_instance_id = ?, holder_token = ?,
                                state = 'ACTIVE', heartbeat_at = ?,
                                expires_at = ?, release_reason = NULL
                            WHERE id = ?
                            """,
                            (
                                self.instance_id,
                                holder_token,
                                timestamp,
                                expires_at,
                                existing["id"],
                            ),
                        )
                        lease_id = str(existing["id"])
                    else:
                        raise TargetOperationLeaseConflict(
                            operation_id=str(existing["operation_id"]),
                            owner_kind=str(existing["owner_kind"]),
                            stale=parked or expired,
                        )
                row = connection.execute(
                    "SELECT * FROM target_operation_leases WHERE id = ?",
                    (lease_id,),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert row is not None
        return dict(row)

    def heartbeat_target_operation_lease(
        self,
        *,
        operation_id: str,
        holder_token: str,
        ttl_seconds: int = 900,
    ) -> None:
        current = datetime.now(timezone.utc)
        with self._connection() as connection, connection:
            cursor = connection.execute(
                """
                UPDATE target_operation_leases
                SET heartbeat_at = ?, expires_at = ?
                WHERE operation_id = ? AND owner_instance_id = ?
                  AND holder_token = ? AND state = 'ACTIVE'
                  AND released_at IS NULL
                """,
                (
                    _utc_text(current),
                    _utc_text(current + timedelta(seconds=ttl_seconds)),
                    operation_id,
                    self.instance_id,
                    holder_token,
                ),
            )
            if cursor.rowcount != 1:
                raise ChangeStoreConflict("target-operation lease ownership was lost")

    def park_target_operation_lease(
        self,
        *,
        operation_id: str,
        holder_token: str,
        reason: str,
    ) -> None:
        timestamp = _utc_now()
        with self._connection() as connection, connection:
            cursor = connection.execute(
                """
                UPDATE target_operation_leases
                SET state = 'STALE_BLOCKED', heartbeat_at = ?,
                    expires_at = ?, release_reason = ?
                WHERE operation_id = ? AND owner_instance_id = ?
                  AND holder_token = ? AND released_at IS NULL
                """,
                (
                    timestamp,
                    timestamp,
                    reason,
                    operation_id,
                    self.instance_id,
                    holder_token,
                ),
            )
            if cursor.rowcount != 1:
                raise ChangeStoreConflict("target-operation lease ownership was lost")

    def release_target_operation_lease(
        self,
        *,
        operation_id: str,
        holder_token: str,
        reason: str,
    ) -> bool:
        timestamp = _utc_now()
        with self._connection() as connection, connection:
            cursor = connection.execute(
                """
                UPDATE target_operation_leases
                SET state = 'RELEASED', released_at = ?, release_reason = ?
                WHERE operation_id = ? AND owner_instance_id = ?
                  AND holder_token = ? AND released_at IS NULL
                """,
                (
                    timestamp,
                    reason,
                    operation_id,
                    self.instance_id,
                    holder_token,
                ),
            )
            return cursor.rowcount == 1

    def mark_previous_instance_target_leases_stale(self) -> int:
        """Fail closed after Host restart; only exact recovery may reclaim."""

        timestamp = _utc_now()
        with self._connection() as connection, connection:
            cursor = connection.execute(
                """
                UPDATE target_operation_leases
                SET state = 'STALE_BLOCKED', heartbeat_at = ?,
                    expires_at = ?, release_reason = ?
                WHERE released_at IS NULL AND state = 'ACTIVE'
                  AND owner_instance_id != ?
                """,
                (
                    timestamp,
                    timestamp,
                    "host_instance_restarted",
                    self.instance_id,
                ),
            )
            return cursor.rowcount


    def supersede_stale_target_lease_for_durable_change_recovery(self) -> int:
        """Let the oldest durable mutation recovery own the global target next.

        Older Host versions could leave both a native target lease and an
        offline-change mutation lease behind.  After restart every prior Host
        target lease is parked as ``STALE_BLOCKED``.  An unreleased mutation
        lease proves that native change execution had already begun, so it is
        the stronger recovery authority.  Release only a different
        previous-instance parked target lease; a current-instance active lease
        remains an explicit fail-closed conflict.
        """

        timestamp = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                mutation = connection.execute(
                    """
                    SELECT leases.transaction_id
                    FROM mutation_leases AS leases
                    JOIN change_transactions AS transactions
                      ON transactions.id = leases.transaction_id
                    WHERE leases.released_at IS NULL
                      AND transactions.state IN (
                          'EXECUTING', 'VERIFYING', 'QUARANTINED'
                      )
                    ORDER BY leases.acquired_at ASC, leases.id ASC
                    LIMIT 1
                    """
                ).fetchone()
                if mutation is None:
                    connection.rollback()
                    return 0
                transaction_id = str(mutation["transaction_id"])
                target = connection.execute(
                    """
                    SELECT id, operation_id, owner_kind, owner_instance_id, state
                    FROM target_operation_leases
                    WHERE scope_key = 'xg5000-host-target'
                      AND released_at IS NULL
                    LIMIT 1
                    """
                ).fetchone()
                if target is None or (
                    str(target["operation_id"]) == transaction_id
                    and str(target["owner_kind"]) == "offline_change"
                ):
                    connection.rollback()
                    return 0
                if (
                    str(target["state"]) != "STALE_BLOCKED"
                    or str(target["owner_instance_id"]) == self.instance_id
                ):
                    connection.rollback()
                    return 0
                cursor = connection.execute(
                    """
                    UPDATE target_operation_leases
                    SET state = 'RELEASED', released_at = ?, release_reason = ?
                    WHERE id = ? AND released_at IS NULL
                      AND state = 'STALE_BLOCKED'
                      AND owner_instance_id != ?
                    """,
                    (
                        timestamp,
                        f"superseded_by_durable_mutation_recovery:{transaction_id}",
                        target["id"],
                        self.instance_id,
                    ),
                )
                connection.commit()
                return cursor.rowcount
            except BaseException:
                connection.rollback()
                raise





    @classmethod
    def _selected_target_recovery_authority_row(
        cls,
        connection: sqlite3.Connection,
    ) -> dict[str, Any] | None:
        """Select one cross-kind recovery authority for the XG5000 target."""

        mutation = connection.execute(
            """
            SELECT leases.transaction_id AS operation_id,
                   transactions.state AS state,
                   transactions.created_at AS created_at,
                   targets.state AS lease_state
            FROM mutation_leases AS leases
            JOIN change_transactions AS transactions
              ON transactions.id = leases.transaction_id
            LEFT JOIN target_operation_leases AS targets
              ON targets.operation_id = leases.transaction_id
             AND targets.owner_kind = 'offline_change'
             AND targets.released_at IS NULL
            WHERE leases.released_at IS NULL
              AND transactions.state IN (
                  'EXECUTING', 'VERIFYING', 'OBSERVING', 'QUARANTINED'
              )
            ORDER BY leases.acquired_at ASC, leases.id ASC
            LIMIT 1
            """
        ).fetchone()
        if mutation is not None:
            return {
                "operation_id": str(mutation["operation_id"]),
                "owner_kind": "offline_change",
                "state": str(mutation["state"]),
                "created_at": str(mutation["created_at"]),
                "lease_state": (
                    str(mutation["lease_state"])
                    if mutation["lease_state"] is not None
                    else None
                ),
                "source": "mutation_lease",
            }

        target = connection.execute(
            """
            SELECT operation_id, owner_kind, state
            FROM target_operation_leases
            WHERE scope_key = 'xg5000-host-target'
              AND released_at IS NULL
            LIMIT 1
            """
        ).fetchone()
        if target is not None:
            operation_id = str(target["operation_id"])
            owner_kind = str(target["owner_kind"])
            if owner_kind == "offline_change":
                change = connection.execute(
                    """
                    SELECT state, created_at FROM change_transactions
                    WHERE id = ?
                      AND state IN ('EXECUTING', 'VERIFYING', 'OBSERVING', 'QUARANTINED')
                    """,
                    (operation_id,),
                ).fetchone()
                if change is not None:
                    return {
                        "operation_id": operation_id,
                        "owner_kind": owner_kind,
                        "state": str(change["state"]),
                        "created_at": str(change["created_at"]),
                        "lease_state": str(target["state"]),
                        "source": "target_operation_lease",
                    }
            pass

        candidates: list[dict[str, Any]] = [
            {
                "operation_id": str(row["id"]),
                "owner_kind": "offline_change",
                "state": str(row["state"]),
                "created_at": str(row["created_at"]),
                "lease_state": None,
                "source": "durable_operation",
            }
            for row in connection.execute(
                """
                SELECT id, state, created_at FROM change_transactions
                WHERE state IN ('EXECUTING', 'VERIFYING', 'OBSERVING', 'QUARANTINED')
                """
            ).fetchall()
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda item: (
                str(item["created_at"]),
                str(item["owner_kind"]),
                str(item["operation_id"]),
            ),
        )

    def selected_target_recovery_authority(self) -> dict[str, Any] | None:
        """Expose the exact cross-kind authority used by the target lease gate."""

        with self._connection() as connection:
            return self._selected_target_recovery_authority_row(connection)

    def active_target_operation_lease(self) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM target_operation_leases
                WHERE released_at IS NULL
                ORDER BY acquired_at LIMIT 1
                """
            ).fetchone()
        return dict(row) if row is not None else None

    def get_model_settings_record(self) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM model_settings WHERE slot = 'default'"
            ).fetchone()
        if row is None:
            return None
        return {
            "protocol": row["protocol"],
            "base_url": row["base_url"],
            "model": row["model"],
            "secret_payload_json": row["secret_payload_json"],
            "timeout_seconds": row["timeout_seconds"],
            "reasoning_effort": row["reasoning_effort"],
            "capability_verified_at": row["capability_verified_at"],
            "model_context_json": row["model_context_json"],
            "updated_at": row["updated_at"],
        }

    def quarantine_model_credential(
        self,
        *,
        reference: str,
        protected_payload: bytes,
        expected_secret_payload_json: str,
        quarantine_payload_json: str,
    ) -> None:
        """Atomically replace a legacy inline secret with a DPAPI quarantine ref."""

        with self._connection() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT secret_payload_json FROM model_settings WHERE slot = 'default'"
            ).fetchone()
            if (
                row is None
                or str(row["secret_payload_json"]) != expected_secret_payload_json
            ):
                raise sqlite3.IntegrityError(
                    "model credential changed before protected migration"
                )
            connection.execute(
                """
                INSERT INTO model_credential_quarantine(
                    reference, protected_payload, created_at
                ) VALUES (?, ?, ?)
                """,
                (reference, protected_payload, _utc_now()),
            )
            updated = connection.execute(
                """
                UPDATE model_settings
                SET secret_payload_json = ?
                WHERE slot = 'default' AND secret_payload_json = ?
                """,
                (quarantine_payload_json, expected_secret_payload_json),
            )
            if updated.rowcount != 1:
                raise sqlite3.IntegrityError(
                    "model credential changed before protected migration"
                )

    def get_model_credential_quarantine(self, reference: str) -> bytes | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT protected_payload FROM model_credential_quarantine
                WHERE reference = ?
                """,
                (reference,),
            ).fetchone()
        return bytes(row["protected_payload"]) if row is not None else None

    def replace_model_credential_quarantine(
        self,
        *,
        reference: str,
        expected_quarantine_payload_json: str,
        replacement_secret_payload_json: str,
    ) -> None:
        """Promote a recovered credential and remove its quarantine atomically."""

        with self._connection() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE model_settings
                SET secret_payload_json = ?
                WHERE slot = 'default' AND secret_payload_json = ?
                """,
                (replacement_secret_payload_json, expected_quarantine_payload_json),
            )
            if updated.rowcount != 1:
                raise sqlite3.IntegrityError(
                    "model credential quarantine is no longer active"
                )
            deleted = connection.execute(
                "DELETE FROM model_credential_quarantine WHERE reference = ?",
                (reference,),
            )
            if deleted.rowcount != 1:
                raise sqlite3.IntegrityError(
                    "model credential quarantine is unavailable"
                )

    def delete_model_credential_quarantine(self, reference: str) -> None:
        with self._connection() as connection, connection:
            connection.execute(
                "DELETE FROM model_credential_quarantine WHERE reference = ?",
                (reference,),
            )

    def upsert_model_settings_record(
        self,
        *,
        protocol: str,
        base_url: str,
        model: str,
        secret_payload_json: str,
        timeout_seconds: float,
        reasoning_effort: str | None = None,
        capability_verified_at: datetime | None = None,
        model_context_json: str | None = None,
    ) -> dict[str, Any]:
        timestamp = _utc_now()
        verified_at = (
            capability_verified_at.astimezone(timezone.utc).isoformat(timespec="milliseconds")
            if capability_verified_at is not None
            else None
        )
        with self._connection() as connection, connection:
            connection.execute(
                """
                INSERT INTO model_settings(
                    slot, protocol, base_url, model, secret_payload_json, timeout_seconds,
                    reasoning_effort, capability_verified_at, model_context_json, updated_at
                )
                VALUES ('default', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slot) DO UPDATE SET
                    protocol = excluded.protocol,
                    base_url = excluded.base_url,
                    model = excluded.model,
                    secret_payload_json = excluded.secret_payload_json,
                    timeout_seconds = excluded.timeout_seconds,
                    reasoning_effort = excluded.reasoning_effort,
                    capability_verified_at = excluded.capability_verified_at,
                    model_context_json = excluded.model_context_json,
                    updated_at = excluded.updated_at
                """,
                (
                    protocol,
                    base_url,
                    model,
                    secret_payload_json,
                    timeout_seconds,
                    reasoning_effort,
                    verified_at,
                    model_context_json,
                    timestamp,
                ),
            )
        record = self.get_model_settings_record()
        assert record is not None
        return record

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        *,
        event_type: str,
        payload: dict[str, Any],
        conversation_id: str | None = None,
        run_id: str | None = None,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO events(
                conversation_id, run_id, event_type, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                run_id,
                event_type,
                _json_dump(payload),
                _utc_now(),
            ),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _conversation_from_row(row: sqlite3.Row) -> Conversation:
        brief = _json_load(row["project_brief_json"])
        keys = set(row.keys())
        handoff = (
            _json_load(str(row["engineering_handoff_json"]))
            if "engineering_handoff_json" in keys
            else {}
        )
        return Conversation(
            id=row["id"],
            title=row["title"],
            workflow=row["workflow"],
            project_brief=brief or None,
            workspace_id=row["workspace_id"],
            revision_id=row["revision_id"],
            engineering_session_id=row["engineering_session_id"],
            engineering_handoff=handoff or None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _engineering_mission_evidence_refs(
        engineering_mission: dict[str, Any],
    ) -> set[str]:
        steps = engineering_mission.get("steps")
        if not isinstance(steps, list):
            raise EngineeringMissionConflict("engineering mission steps are invalid")
        refs: set[str] = set()
        for step in steps:
            if not isinstance(step, dict):
                raise EngineeringMissionConflict("engineering mission step is invalid")
            evidence_refs = step.get("evidenceRefs")
            if not isinstance(evidence_refs, list) or any(
                not isinstance(value, str) or not value for value in evidence_refs
            ):
                raise EngineeringMissionConflict(
                    "engineering mission evidenceRefs are invalid"
                )
            refs.update(evidence_refs)
        return refs

    @staticmethod
    def _engineering_mission_from_row(row: sqlite3.Row) -> EngineeringMission:
        return EngineeringMission(
            conversation_id=row["conversation_id"],
            workspace_id=row["workspace_id"],
            revision_id=row["revision_id"],
            revision=row["revision"],
            engineering_mission=_json_load(str(row["snapshot_json"])),
            digest=row["digest"],
            updated_by_run_id=row["updated_by_run_id"],
            change_transaction_id=row["change_transaction_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _engineering_agent_checkpoint_from_row(
        row: sqlite3.Row,
    ) -> EngineeringAgentCheckpoint:
        return EngineeringAgentCheckpoint(
            conversation_id=row["conversation_id"],
            workspace_id=row["workspace_id"],
            revision_id=row["revision_id"],
            mission_digest=row["mission_digest"],
            cycle=row["cycle"],
            checkpoint=_json_load(str(row["checkpoint_json"])),
            digest=row["digest"],
            updated_by_run_id=row["updated_by_run_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _engineering_agent_transcript_from_row(row: sqlite3.Row) -> EngineeringAgentTranscript:
        return EngineeringAgentTranscript(
            source_run_id=row["source_run_id"], conversation_id=row["conversation_id"],
            workspace_id=row["workspace_id"], revision_id=row["revision_id"],
            request_message_id=row["request_message_id"], cursor=row["cursor"],
            checkpoint=_json_load(row["checkpoint_json"]), digest=row["digest"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> Message:
        review = None
        if (
            "review_plan_digest" in set(row.keys())
            and row["review_plan_digest"] is not None
        ):
            review = ProjectPlanReview(
                schema_version=row["review_schema_version"],
                conversation_id=row["conversation_id"],
                message_id=row["id"],
                plan_digest=row["review_plan_digest"],
                reviewed_section_ids=json.loads(
                    str(row["review_reviewed_section_ids_json"])
                ),
                reviewed_item_ids=json.loads(
                    str(row["review_reviewed_item_ids_json"])
                ),
                project_brief_digest=row["review_project_brief_digest"],
                attachment_revision_digest=row[
                    "review_attachment_revision_digest"
                ],
                actor_id=row["review_actor_id"],
                reviewed_at=row["review_reviewed_at"],
            )
        return Message(
            id=row["id"],
            conversation_id=row["conversation_id"],
            sequence=row["sequence"],
            role=row["role"],
            content=row["content"],
            metadata=_json_load(row["metadata_json"]),
            created_at=row["created_at"],
            project_plan_review=review,
        )

    @staticmethod
    def _project_plan_review_from_row(row: sqlite3.Row) -> ProjectPlanReview:
        return ProjectPlanReview(
            schema_version=row["schema_version"],
            conversation_id=row["conversation_id"],
            message_id=row["message_id"],
            plan_digest=row["plan_digest"],
            reviewed_section_ids=HostStore._reviewed_section_ids_from_row(row),
            reviewed_item_ids=HostStore._reviewed_item_ids_from_row(row),
            project_brief_digest=row["project_brief_digest"],
            attachment_revision_digest=row["attachment_revision_digest"],
            actor_id=row["actor_id"],
            reviewed_at=row["reviewed_at"],
        )

    @staticmethod
    def _project_plan_section_ids(artifact: dict[str, Any]) -> list[str]:
        sections = artifact.get("sections")
        if not isinstance(sections, list) or not sections:
            raise ProjectPlanReviewConflict(
                "project plan must contain reviewable sections"
            )
        section_ids: list[str] = []
        for section in sections:
            section_id = (
                section.get("sectionId") if isinstance(section, dict) else None
            )
            if not isinstance(section_id, str) or not section_id:
                raise ProjectPlanReviewConflict(
                    "project plan contains an invalid section identity"
                )
            if section_id in section_ids:
                raise ProjectPlanReviewConflict(
                    "project plan contains duplicate section identities"
                )
            section_ids.append(section_id)
        return section_ids

    @staticmethod
    def _normalize_reviewed_section_ids(values: list[str]) -> list[str]:
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value for value in values)
            or len(set(values)) != len(values)
        ):
            raise ProjectPlanReviewConflict(
                "reviewed section identities must be a unique non-empty list"
            )
        return list(values)

    @staticmethod
    def _reviewed_section_ids_from_row(row: sqlite3.Row) -> list[str]:
        raw = row["reviewed_section_ids_json"]
        loaded = json.loads(str(raw))
        if not isinstance(loaded, list) or any(
            not isinstance(value, str) for value in loaded
        ):
            raise ProjectPlanReviewConflict(
                "stored reviewed section identities are invalid"
            )
        return loaded

    @staticmethod
    def _normalize_reviewed_item_ids(values: list[str]) -> list[str]:
        if (
            not isinstance(values, list)
            or not values
            or any(
                not isinstance(value, str) or not value or len(value) > 2_000
                for value in values
            )
            or len(set(values)) != len(values)
        ):
            raise ProjectPlanReviewConflict(
                "reviewed item identities must be a unique non-empty list"
            )
        return list(values)

    @staticmethod
    def _reviewed_item_ids_from_row(row: sqlite3.Row) -> list[str]:
        raw = row["reviewed_item_ids_json"]
        loaded = json.loads(str(raw))
        if not isinstance(loaded, list) or any(
            not isinstance(value, str) for value in loaded
        ):
            raise ProjectPlanReviewConflict(
                "stored reviewed item identities are invalid"
            )
        return loaded

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> Run:
        return Run(
            id=row["id"],
            conversation_id=row["conversation_id"],
            operation_id=row["operation_id"],
            state=row["state"],
            request_message_id=row["request_message_id"],
            metadata=_json_load(row["metadata_json"]),
            error_code=row["error_code"],
            error_detail=row["error_detail"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _approval_row(
        connection: sqlite3.Connection,
        transaction_id: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM change_approvals WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()

    @staticmethod
    def _approval_from_row(row: sqlite3.Row) -> ChangeApproval:
        return ChangeApproval(
            transaction_id=row["transaction_id"],
            decision=row["decision"],
            plan_digest=row["plan_digest"],
            actor_id=row["actor_id"],
            decided_at=row["decided_at"],
        )

    @classmethod
    def _change_transaction_from_row(
        cls,
        row: sqlite3.Row,
        approval_row: sqlite3.Row | None,
    ) -> ChangeTransaction:
        return ChangeTransaction(
            id=row["id"],
            operation_id=row["operation_id"],
            conversation_id=row["conversation_id"],
            run_id=row["run_id"],
            state=row["state"],
            target=ChangeTargetIdentity.model_validate(
                _json_load(row["target_identity_json"])
            ),
            execution_target=(
                ChangeTargetIdentity.model_validate(_json_load(row["execution_target_json"]))
                if row["execution_target_json"] is not None else None
            ),
            mutation_scope=row["mutation_scope"],
            risk_level=row["risk_level"],
            intent=row["intent"],
            plan=_json_load(row["plan_json"]),
            plan_digest=row["plan_digest"],
            requested_by_actor_id=row["requested_by_actor_id"],
            approval_expires_at=row["approval_expires_at"],
            approval=(
                cls._approval_from_row(approval_row)
                if approval_row is not None
                else None
            ),
            result=_json_load(row["result_json"]),
            error_code=row["error_code"],
            error_detail=row["error_detail"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            execution_started_at=row["execution_started_at"],
            verification_started_at=row["verification_started_at"],
            completed_at=row["completed_at"],
            receipt_acknowledged_at=row["receipt_acknowledged_at"],
            receipt_acknowledged_by=row["receipt_acknowledged_by"],
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> StoredEvent:
        return StoredEvent(
            sequence=row["sequence"],
            conversation_id=row["conversation_id"],
            run_id=row["run_id"],
            event_type=row["event_type"],
            payload=_json_load(row["payload_json"]),
            created_at=row["created_at"],
        )
