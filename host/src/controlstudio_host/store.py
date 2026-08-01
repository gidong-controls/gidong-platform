from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import (
    ApprovalDecision,
    ChangeApproval,
    ChangeRiskLevel,
    ChangeTargetIdentity,
    ChangeTransaction,
    ChangeTransactionState,
    Conversation,
    ConversationDetail,
    Message,
    MessageRole,
    ProjectPlanReview,
    Run,
    RunState,
    StoredEvent,
)


class ChangeStoreConflict(RuntimeError):
    """A durable change contract conflicted with existing server authority."""


class ConversationWorkflowConflict(RuntimeError):
    """A message attempted to change a conversation's fixed workflow."""


class ConversationActiveRunConflict(RuntimeError):
    """A conversation already owns a non-terminal agent run."""


class ProjectPlanReviewConflict(RuntimeError):
    """A plan review did not match the latest reviewable server artifact."""


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
        "backup_id",
        "mcp_plan_id",
        "mcp_plan_digest",
        "session_id",
        "workspace_id",
        "project_path",
        "project_hash",
        "project_identity_digest",
        "program_id",
        "token_offset",
        "expected",
        "replacement",
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
)


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
                if version in applied:
                    continue
                with connection:
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
        project_brief: dict[str, str] | None = None,
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
                    project_plan_reviews.actor_id AS review_actor_id,
                    project_plan_reviews.reviewed_at AS review_reviewed_at,
                    project_plan_reviews.reviewed_section_ids_json
                        AS review_reviewed_section_ids_json
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
                    "SELECT workflow FROM conversations WHERE id = ?",
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
                newest_sequence = int(
                    connection.execute(
                        """
                        SELECT COALESCE(MAX(sequence), 0)
                        FROM messages
                        WHERE conversation_id = ?
                        """,
                        (conversation_id,),
                    ).fetchone()[0]
                )
                if int(latest["sequence"]) != newest_sequence:
                    raise ProjectPlanReviewConflict(
                        "project plan is stale because the conversation changed"
                    )

                metadata = _json_load(str(latest["metadata_json"]))
                artifact = metadata.get("plan_artifact")
                stored_digest = metadata.get("plan_digest")
                if (
                    not isinstance(artifact, dict)
                    or artifact.get("schemaVersion") != "1"
                ):
                    raise ProjectPlanReviewConflict(
                        "latest project plan artifact is invalid"
                    )
                if (
                    metadata.get("plan_status") != "ready_for_review"
                    or artifact.get("status") != "ready_for_review"
                ):
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
                if set(supplied_section_ids) != set(expected_section_ids):
                    raise ProjectPlanReviewConflict(
                        "every project plan section must be explicitly reviewed"
                    )
                canonical_reviewed_section_ids = expected_section_ids

                existing = connection.execute(
                    """
                    SELECT * FROM project_plan_reviews
                    WHERE message_id = ? AND plan_digest = ?
                    """,
                    (message_id, plan_digest),
                ).fetchone()
                if existing is not None:
                    existing_section_ids = self._reviewed_section_ids_from_row(
                        existing
                    )
                    if existing_section_ids != canonical_reviewed_section_ids:
                        raise ProjectPlanReviewConflict(
                            "stored project plan review does not cover every section"
                        )
                    connection.rollback()
                    return self._project_plan_review_from_row(existing), False

                timestamp = _utc_now()
                connection.execute(
                    """
                    INSERT INTO project_plan_reviews(
                        message_id, plan_digest, conversation_id,
                        actor_id, reviewed_at, reviewed_section_ids_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
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
    ) -> tuple[ProjectPlanReview, dict[str, Any]]:
        """Re-read and prove the exact latest reviewed plan before creation."""

        with self._connection() as connection:
            conversation = connection.execute(
                "SELECT workflow FROM conversations WHERE id = ?",
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
            newest_sequence = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0)
                    FROM messages
                    WHERE conversation_id = ?
                    """,
                    (conversation_id,),
                ).fetchone()[0]
            )
            if (
                latest is None
                or latest["id"] != message_id
                or int(latest["sequence"]) != newest_sequence
            ):
                raise ProjectPlanReviewConflict(
                    "reviewed project plan is stale; review the latest plan"
                )
            metadata = _json_load(str(latest["metadata_json"]))
            artifact = metadata.get("plan_artifact")
            if not isinstance(artifact, dict):
                raise ProjectPlanReviewConflict(
                    "latest project plan artifact is invalid"
                )
            actual_digest = hashlib.sha256(
                json.dumps(
                    artifact,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            if (
                metadata.get("plan_status") != "ready_for_review"
                or artifact.get("status") != "ready_for_review"
                or metadata.get("plan_digest") != actual_digest
                or plan_digest != actual_digest
            ):
                raise ProjectPlanReviewConflict(
                    "reviewed project plan digest or readiness no longer matches"
                )
            expected_section_ids = self._project_plan_section_ids(artifact)
            supplied_section_ids = self._normalize_reviewed_section_ids(
                reviewed_section_ids
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
            stored_section_ids = self._reviewed_section_ids_from_row(review_row)
            if (
                stored_section_ids != expected_section_ids
                or supplied_section_ids != expected_section_ids
            ):
                raise ProjectPlanReviewConflict(
                    "reviewed project plan section scope does not match"
                )
            return self._project_plan_review_from_row(review_row), artifact

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

    def create_run(
        self,
        *,
        conversation_id: str,
        state: RunState = RunState.IDLE,
        request_message_id: str | None = None,
        operation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Run | None:
        run_id = str(uuid.uuid4())
        timestamp = _utc_now()
        metadata = metadata or {}
        with self._connection() as connection, connection:
            exists = connection.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if exists is None:
                return None
            connection.execute(
                """
                INSERT INTO runs(
                    id, conversation_id, operation_id, state, request_message_id,
                    metadata_json, error_code, error_detail, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    run_id,
                    conversation_id,
                    operation_id,
                    state.value,
                    request_message_id,
                    _json_dump(metadata),
                    timestamp,
                    timestamp,
                ),
            )
            self._insert_event(
                connection,
                conversation_id=conversation_id,
                run_id=run_id,
                event_type="run.created",
                payload={
                    "conversation_id": conversation_id,
                    "run_id": run_id,
                    "state": state.value,
                },
            )
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        assert row is not None
        return self._run_from_row(row)

    def create_user_message_and_run(
        self,
        *,
        conversation_id: str,
        content: str,
        operation_id: str,
        workflow: str = "engineering",
        project_brief: dict[str, str] | None = None,
        message_metadata: dict[str, Any] | None = None,
    ) -> tuple[Message | None, Run | None, bool]:
        """Atomically append one user message and its idempotent analysis run."""

        message_metadata = message_metadata or {}
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
                    VALUES (?, ?, ?, 'user', ?, ?, ?)
                    """,
                    (
                        message_id,
                        conversation_id,
                        message_sequence,
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
                    VALUES (?, ?, ?, ?, ?, '{}', NULL, NULL, ?, ?)
                    """,
                    (
                        run_id,
                        conversation_id,
                        operation_id,
                        RunState.ANALYZING.value,
                        message_id,
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
                        "role": MessageRole.USER.value,
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
                "SELECT * FROM runs WHERE operation_id = ?", (operation_id,)
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
    ) -> tuple[ChangeTransaction, bool]:
        """Create one immutable approval contract, idempotent by operation id."""

        transaction_id = str(uuid.uuid4())
        timestamp = _utc_now()
        expires_at = _utc_text(approval_expires_at)
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
                        ?, ?, ?, ?, 'PENDING_APPROVAL',
                        ?, ?, ?, ?, ?, ?, ?, ?, '{}', NULL, NULL, ?, ?, NULL, NULL, NULL
                    )
                    """,
                    (
                        transaction_id,
                        operation_id,
                        conversation_id,
                        run_id,
                        _json_dump(target.model_dump(mode="json")),
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
                event_payload = {
                    "transaction_id": transaction_id,
                    "operation_id": operation_id,
                    "state": ChangeTransactionState.PENDING_APPROVAL.value,
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
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert row is not None
        return self._change_transaction_from_row(row, None), True

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
                    SET state = 'EXECUTING', updated_at = ?, execution_started_at = ?
                    WHERE id = ?
                    """,
                    (timestamp, timestamp, transaction_id),
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
            ChangeTransactionState.COMPLETED,
            ChangeTransactionState.ROLLED_BACK,
            ChangeTransactionState.FAILED,
            ChangeTransactionState.QUARANTINED,
        }
        if state not in allowed_final_states:
            raise ValueError("execution outcome must be a terminal change state")
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
                else:
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
                        "lease_released": state is not ChangeTransactionState.QUARANTINED,
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

    def reconcile_interrupted_change_transactions(
        self,
        *,
        now: datetime | None = None,
    ) -> int:
        """Expire stale approvals and quarantine mutation stages after restart."""

        timestamp = _utc_text(now or datetime.now(timezone.utc))
        reconciled = 0
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    """
                    SELECT * FROM change_transactions
                    WHERE state IN ('PENDING_APPROVAL', 'EXECUTING', 'VERIFYING')
                    """
                ).fetchall()
                for row in rows:
                    previous_state = ChangeTransactionState(str(row["state"]))
                    next_state: ChangeTransactionState | None = None
                    error_code: str | None = None
                    if (
                        previous_state is ChangeTransactionState.PENDING_APPROVAL
                        and str(row["approval_expires_at"]) <= timestamp
                    ):
                        next_state = ChangeTransactionState.EXPIRED
                        error_code = "approval_expired"
                    elif previous_state in {
                        ChangeTransactionState.EXECUTING,
                        ChangeTransactionState.VERIFYING,
                    }:
                        next_state = ChangeTransactionState.QUARANTINED
                        error_code = "interrupted_change_outcome_uncertain"
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

    def resolve_quarantined_change(
        self,
        *,
        transaction_id: str,
        state: ChangeTransactionState,
        evidence: dict[str, Any],
        actor_id: str,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> ChangeTransaction | None:
        allowed = {
            ChangeTransactionState.COMPLETED,
            ChangeTransactionState.ROLLED_BACK,
            ChangeTransactionState.QUARANTINED,
        }
        if state not in allowed:
            raise ValueError("quarantine resolution has an unsupported state")
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
                else:
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

    def release_target_operation_lease_by_operation(
        self,
        *,
        operation_id: str,
        reason: str,
    ) -> bool:
        timestamp = _utc_now()
        with self._connection() as connection, connection:
            cursor = connection.execute(
                """
                UPDATE target_operation_leases
                SET state = 'RELEASED', released_at = ?, release_reason = ?
                WHERE operation_id = ? AND released_at IS NULL
                """,
                (timestamp, reason, operation_id),
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

    def release_terminal_native_target_operation_leases(self) -> int:
        """Release only native operations durably proven terminal on startup."""

        timestamp = _utc_now()
        with self._connection() as connection, connection:
            table = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'native_project_operations'
                """
            ).fetchone()
            if table is None:
                return 0
            cursor = connection.execute(
                """
                UPDATE target_operation_leases
                SET state = 'RELEASED', released_at = ?,
                    release_reason = 'native_project_terminal_on_startup'
                WHERE owner_kind = 'native_project_create'
                  AND released_at IS NULL
                  AND operation_id IN (
                      SELECT operation_id FROM native_project_operations
                      WHERE state IN ('COMPLETED', 'FAILED')
                  )
                """,
                (timestamp,),
            )
            return cursor.rowcount

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

    def active_mutation_lease(self, mutation_scope: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT mutation_scope, transaction_id, acquired_at
                FROM mutation_leases
                WHERE mutation_scope = ? AND released_at IS NULL
                """,
                (mutation_scope,),
            ).fetchone()
        if row is None:
            return None
        return {
            "mutation_scope": row["mutation_scope"],
            "transaction_id": row["transaction_id"],
            "acquired_at": row["acquired_at"],
        }

    def any_active_mutation_lease(self) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT mutation_scope, transaction_id, acquired_at
                FROM mutation_leases
                WHERE released_at IS NULL
                ORDER BY acquired_at
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return {
            "mutation_scope": row["mutation_scope"],
            "transaction_id": row["transaction_id"],
            "acquired_at": row["acquired_at"],
        }

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
            "secret_ref": row["secret_ref"],
            "timeout_seconds": row["timeout_seconds"],
            "capability_verified_at": row["capability_verified_at"],
            "updated_at": row["updated_at"],
        }

    def upsert_model_settings_record(
        self,
        *,
        protocol: str,
        base_url: str,
        model: str,
        secret_ref: str,
        timeout_seconds: float,
        capability_verified_at: datetime | None = None,
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
                    slot, protocol, base_url, model, secret_ref,
                    timeout_seconds, capability_verified_at, updated_at
                )
                VALUES ('default', ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slot) DO UPDATE SET
                    protocol = excluded.protocol,
                    base_url = excluded.base_url,
                    model = excluded.model,
                    secret_ref = excluded.secret_ref,
                    timeout_seconds = excluded.timeout_seconds,
                    capability_verified_at = excluded.capability_verified_at,
                    updated_at = excluded.updated_at
                """,
                (
                    protocol,
                    base_url,
                    model,
                    secret_ref,
                    timeout_seconds,
                    verified_at,
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
        return Conversation(
            id=row["id"],
            title=row["title"],
            workflow=row["workflow"],
            project_brief=brief or None,
            workspace_id=row["workspace_id"],
            revision_id=row["revision_id"],
            engineering_session_id=row["engineering_session_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> Message:
        review = None
        if (
            "review_plan_digest" in set(row.keys())
            and row["review_plan_digest"] is not None
        ):
            review = ProjectPlanReview(
                conversation_id=row["conversation_id"],
                message_id=row["id"],
                plan_digest=row["review_plan_digest"],
                reviewed_section_ids=json.loads(
                    str(row["review_reviewed_section_ids_json"])
                ),
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
            conversation_id=row["conversation_id"],
            message_id=row["message_id"],
            plan_digest=row["plan_digest"],
            reviewed_section_ids=HostStore._reviewed_section_ids_from_row(row),
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
