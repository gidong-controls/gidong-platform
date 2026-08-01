from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from .mcp_client import MCPToolError

NATIVE_PROJECT_AUTHORIZATION_SCHEMA = "controlstudio-native-project-authorization/v1"
NATIVE_PROJECT_PLAN_SCHEMA = "controlstudio-native-project-plan/v1"
_INVALID_FILE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_INVALID_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_RESERVED_WINDOWS_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


class NativeProjectConflict(RuntimeError):
    """An idempotency or state precondition conflicted with server authority."""


class NativeProjectPreconditionError(RuntimeError):
    """The requested local path or XG5000 target was not safe to use."""


class NativeProjectEvidenceError(RuntimeError):
    """MCP returned evidence that did not prove the requested native project."""


@dataclass(frozen=True)
class _NativeProjectRequest:
    """Public product-contract values; contains no vendor adapter behavior."""

    project_name: str
    destination_directory: Path
    plc_series: str
    cpu_type: str
    program_name: str
    program_language: str
    description: str


def _validated_text(field: str, value: object, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise NativeProjectPreconditionError(f"{field} must be a string")
    result = value.strip()
    if not result or len(result) > maximum or _INVALID_TEXT.search(result):
        raise NativeProjectPreconditionError(
            f"{field} is empty, too long, or contains a control character"
        )
    return result


def _validated_name(field: str, value: object, *, maximum: int) -> str:
    result = _validated_text(field, value, maximum=maximum)
    if _INVALID_FILE_NAME.search(result) or result.endswith((".", " ")):
        raise NativeProjectPreconditionError(
            f"{field} contains a character unavailable to XG5000 or Windows"
        )
    if field == "project_name" and result.casefold() in _RESERVED_WINDOWS_NAMES:
        raise NativeProjectPreconditionError("project_name is a reserved Windows name")
    return result


def _validate_native_project_request(request: _NativeProjectRequest) -> _NativeProjectRequest:
    project_name = _validated_name("project_name", request.project_name, maximum=80)
    program_name = _validated_name("program_name", request.program_name, maximum=80)
    plc_series = _validated_text("plc_series", request.plc_series, maximum=32)
    cpu_type = _validated_text("cpu_type", request.cpu_type, maximum=64)
    language = _validated_text("program_language", request.program_language, maximum=32)
    if not isinstance(request.description, str):
        raise NativeProjectPreconditionError("description must be a string")
    description = request.description.strip()
    if len(description) > 1024 or "\x00" in description:
        raise NativeProjectPreconditionError("description is too long or contains NUL")
    raw_directory = Path(request.destination_directory).expanduser()
    if not raw_directory.is_absolute():
        raise NativeProjectPreconditionError("destination_directory must be absolute")
    try:
        directory = raw_directory.resolve(strict=True)
    except OSError as exc:
        raise NativeProjectPreconditionError("destination_directory does not exist") from exc
    if not directory.is_dir() or directory.is_symlink():
        raise NativeProjectPreconditionError("destination_directory must be a real directory")
    try:
        contents = tuple(directory.iterdir())
    except OSError as exc:
        raise NativeProjectPreconditionError(
            "destination_directory could not be enumerated"
        ) from exc
    if contents:
        raise NativeProjectPreconditionError("destination_directory must be empty")
    return _NativeProjectRequest(
        project_name=project_name,
        destination_directory=directory,
        plc_series=plc_series,
        cpu_type=cpu_type,
        program_name=program_name,
        program_language=language,
        description=description,
    )


def _native_project_plan_digest(
    request: _NativeProjectRequest,
    *,
    pid: int,
    hwnd: int,
) -> str:
    validated = _validate_native_project_request(request)
    if pid <= 0 or hwnd <= 0:
        raise NativeProjectPreconditionError("pid and hwnd must be positive integers")
    payload = {
        "schema": NATIVE_PROJECT_PLAN_SCHEMA,
        "operation": "project.create_native",
        "pid": pid,
        "hwnd": hwnd,
        "project_name": validated.project_name,
        "destination_directory": os.path.normcase(str(validated.destination_directory)),
        "plc_series": validated.plc_series,
        "cpu_type": validated.cpu_type,
        "program_name": validated.program_name,
        "program_language": validated.program_language,
        "description": validated.description,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class NativeProjectSpec:
    operation_id: str
    project_name: str
    workspace_parent: Path
    plc_series: str
    cpu_model: str
    program_name: str
    language: str
    description: str
    xg5000_pid: int
    xg5000_hwnd: int
    planning_conversation_id: str | None = None
    planning_message_id: str | None = None
    planning_plan_digest: str | None = None
    reviewed_section_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class NativeProjectOperation:
    id: str
    operation_id: str
    state: str
    request_digest: str
    actor_id: str
    request: dict[str, Any]
    plan_digest: str | None
    destination_directory: str
    result: dict[str, Any]
    error_code: str | None
    error_detail: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class NativeProjectRecoveryEvidence:
    pid: int
    hwnd: int
    expected_project_path: Path
    expected_project_sha256: str | None


class NativeProjectGateway(Protocol):
    async def call_read_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> Any: ...

    async def call_workspace_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> Any: ...

    def sign_mutation_authorization(
        self,
        claims: dict[str, Any],
    ) -> dict[str, Any]: ...

    def bind_agent_session(self, session: dict[str, Any]) -> None: ...

    def clear_agent_session(self) -> None: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _json_dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_load(value: str) -> dict[str, Any]:
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise TypeError("stored native-project JSON is not an object")
    return loaded


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_path(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _require_within(path: Path, parent: Path, field: str) -> None:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError as exc:
        raise NativeProjectEvidenceError(
            f"{field} escaped the Host-selected workspace directory"
        ) from exc


def _canonical_uuid(value: str, field: str) -> str:
    try:
        normalized = str(uuid.UUID(value))
    except ValueError as exc:
        raise NativeProjectPreconditionError(f"{field} must be a UUID") from exc
    if normalized != value:
        raise NativeProjectPreconditionError(
            f"{field} must use canonical UUID form"
        )
    return normalized


class NativeProjectRepository:
    """Durable idempotency and crash quarantine for native UI creation."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
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
        with self._connection() as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS native_project_operations (
                    id TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL CHECK (
                        state IN (
                            'ALLOCATED', 'EXECUTING', 'COMPLETED',
                            'FAILED', 'QUARANTINED'
                        )
                    ),
                    request_digest TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    plan_digest TEXT,
                    destination_directory TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT,
                    error_detail TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_native_project_operations_state
                ON native_project_operations(state, updated_at)
                """
            )

    def allocate(
        self,
        *,
        operation_id: str,
        request_digest: str,
        actor_id: str,
        request: dict[str, Any],
        destination_directory: str,
    ) -> tuple[NativeProjectOperation, bool]:
        operation_id = _canonical_uuid(operation_id, "operation_id")
        timestamp = _utc_text(_utc_now())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    """
                    SELECT * FROM native_project_operations
                    WHERE operation_id = ?
                    """,
                    (operation_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        str(existing["request_digest"]) != request_digest
                        or str(existing["actor_id"]) != actor_id
                    ):
                        raise NativeProjectConflict(
                            "operationId is already bound to another actor or request"
                        )
                    connection.rollback()
                    return self._from_row(existing), False
                connection.execute(
                    """
                    INSERT INTO native_project_operations(
                        id, operation_id, state, request_digest, actor_id,
                        request_json, plan_digest, destination_directory,
                        result_json, error_code, error_detail,
                        created_at, updated_at, completed_at
                    )
                    VALUES (?, ?, 'ALLOCATED', ?, ?, ?, NULL, ?, '{}',
                            NULL, NULL, ?, ?, NULL)
                    """,
                    (
                        operation_id,
                        operation_id,
                        request_digest,
                        actor_id,
                        _json_dump(request),
                        destination_directory,
                        timestamp,
                        timestamp,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM native_project_operations WHERE id = ?",
                    (operation_id,),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert row is not None
        return self._from_row(row), True

    def set_plan_digest(
        self,
        operation_id: str,
        plan_digest: str,
    ) -> NativeProjectOperation:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM native_project_operations WHERE id = ?",
                    (operation_id,),
                ).fetchone()
                if row is None:
                    raise NativeProjectConflict("native project operation not found")
                existing = row["plan_digest"]
                if existing is not None and str(existing) != plan_digest:
                    raise NativeProjectConflict(
                        "native project plan digest changed after allocation"
                    )
                if str(row["state"]) != "ALLOCATED":
                    if existing == plan_digest:
                        connection.rollback()
                        return self._from_row(row)
                    raise NativeProjectConflict(
                        "native project plan cannot change after execution begins"
                    )
                timestamp = _utc_text(_utc_now())
                connection.execute(
                    """
                    UPDATE native_project_operations
                    SET plan_digest = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (plan_digest, timestamp, operation_id),
                )
                updated = connection.execute(
                    "SELECT * FROM native_project_operations WHERE id = ?",
                    (operation_id,),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert updated is not None
        return self._from_row(updated)

    def begin_execution(self, operation_id: str) -> bool:
        timestamp = _utc_text(_utc_now())
        with self._connection() as connection, connection:
            cursor = connection.execute(
                """
                UPDATE native_project_operations
                SET state = 'EXECUTING', updated_at = ?
                WHERE id = ? AND state = 'ALLOCATED' AND plan_digest IS NOT NULL
                """,
                (timestamp, operation_id),
            )
            return cursor.rowcount == 1

    def complete(
        self,
        operation_id: str,
        result: dict[str, Any],
    ) -> NativeProjectOperation:
        return self._terminal(
            operation_id,
            expected_state="EXECUTING",
            state="COMPLETED",
            result=result,
            error_code=None,
            error_detail=None,
        )

    def fail_allocated(
        self,
        operation_id: str,
        *,
        error_code: str,
        error_detail: str,
    ) -> NativeProjectOperation:
        return self._terminal(
            operation_id,
            expected_state="ALLOCATED",
            state="FAILED",
            result={},
            error_code=error_code,
            error_detail=error_detail,
        )

    def quarantine(
        self,
        operation_id: str,
        *,
        error_code: str,
        error_detail: str,
    ) -> NativeProjectOperation:
        return self._terminal(
            operation_id,
            expected_state="EXECUTING",
            state="QUARANTINED",
            result={},
            error_code=error_code,
            error_detail=error_detail,
        )

    def resolve_quarantine(
        self,
        operation_id: str,
        *,
        state: str,
        result: dict[str, Any],
        error_code: str | None,
        error_detail: str | None,
    ) -> NativeProjectOperation:
        if state not in {"COMPLETED", "FAILED", "QUARANTINED"}:
            raise ValueError("unsupported native-project recovery state")
        if state == "QUARANTINED":
            timestamp = _utc_text(_utc_now())
            with self._connection() as connection, connection:
                cursor = connection.execute(
                    """
                    UPDATE native_project_operations
                    SET result_json = ?, error_code = ?, error_detail = ?,
                        updated_at = ?, completed_at = NULL
                    WHERE id = ? AND state = 'QUARANTINED'
                    """,
                    (
                        _json_dump(result),
                        error_code,
                        error_detail,
                        timestamp,
                        operation_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise NativeProjectConflict(
                        "native project operation is not quarantined"
                    )
                row = connection.execute(
                    "SELECT * FROM native_project_operations WHERE id = ?",
                    (operation_id,),
                ).fetchone()
            assert row is not None
            return self._from_row(row)
        return self._terminal(
            operation_id,
            expected_state="QUARANTINED",
            state=state,
            result=result,
            error_code=error_code,
            error_detail=error_detail,
        )

    def reconcile_interrupted(self) -> int:
        timestamp = _utc_text(_utc_now())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            allocated = connection.execute(
                """
                UPDATE native_project_operations
                SET state = 'FAILED',
                    error_code = 'HOST_RESTARTED_BEFORE_NATIVE_PROJECT_EXECUTION',
                    error_detail = ?,
                    updated_at = ?,
                    completed_at = ?
                WHERE state = 'ALLOCATED'
                """,
                (
                    (
                        "Host restarted before native UI execution began; "
                        "no mutation was dispatched."
                    ),
                    timestamp,
                    timestamp,
                ),
            )
            executing = connection.execute(
                """
                UPDATE native_project_operations
                SET state = 'QUARANTINED',
                    error_code = 'HOST_RESTARTED_DURING_NATIVE_PROJECT_CREATION',
                    error_detail = ?,
                    updated_at = ?,
                    completed_at = ?
                WHERE state = 'EXECUTING'
                """,
                (
                    (
                        "Host restarted after native UI execution began; "
                        "the operation will not be retried automatically."
                    ),
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()
            return allocated.rowcount + executing.rowcount

    def get(self, operation_id: str) -> NativeProjectOperation | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM native_project_operations WHERE id = ?",
                (operation_id,),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def blocking_operation(self) -> NativeProjectOperation | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM native_project_operations
                WHERE state IN ('EXECUTING', 'QUARANTINED')
                ORDER BY updated_at DESC LIMIT 1
                """
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def _terminal(
        self,
        operation_id: str,
        *,
        expected_state: str,
        state: str,
        result: dict[str, Any],
        error_code: str | None,
        error_detail: str | None,
    ) -> NativeProjectOperation:
        timestamp = _utc_text(_utc_now())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE native_project_operations
                    SET state = ?, result_json = ?, error_code = ?,
                        error_detail = ?, updated_at = ?, completed_at = ?
                    WHERE id = ? AND state = ?
                    """,
                    (
                        state,
                        _json_dump(result),
                        error_code,
                        error_detail,
                        timestamp,
                        timestamp,
                        operation_id,
                        expected_state,
                    ),
                )
                if cursor.rowcount != 1:
                    raise NativeProjectConflict(
                        f"native project operation is not in {expected_state}"
                    )
                updated = connection.execute(
                    "SELECT * FROM native_project_operations WHERE id = ?",
                    (operation_id,),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert updated is not None
        return self._from_row(updated)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> NativeProjectOperation:
        return NativeProjectOperation(
            id=str(row["id"]),
            operation_id=str(row["operation_id"]),
            state=str(row["state"]),
            request_digest=str(row["request_digest"]),
            actor_id=str(row["actor_id"]),
            request=_json_load(str(row["request_json"])),
            plan_digest=(
                str(row["plan_digest"]) if row["plan_digest"] is not None else None
            ),
            destination_directory=str(row["destination_directory"]),
            result=_json_load(str(row["result_json"])),
            error_code=(
                str(row["error_code"]) if row["error_code"] is not None else None
            ),
            error_detail=(
                str(row["error_detail"]) if row["error_detail"] is not None else None
            ),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            completed_at=(
                datetime.fromisoformat(str(row["completed_at"]))
                if row["completed_at"] is not None
                else None
            ),
        )


class NativeProjectService:
    """Host-owned, idempotent native XG5000 project creation path."""

    def __init__(
        self,
        repository: NativeProjectRepository,
        gateway: NativeProjectGateway,
    ) -> None:
        self._repository = repository
        self._gateway = gateway

    def replay_if_exists(
        self,
        spec: NativeProjectSpec,
        *,
        actor_id: str,
    ) -> NativeProjectOperation | None:
        operation_id = _canonical_uuid(spec.operation_id, "operationId")
        existing = self._repository.get(operation_id)
        if existing is None:
            return None
        raw_parent = Path(spec.workspace_parent).expanduser()
        if not raw_parent.is_absolute():
            raise NativeProjectConflict(
                "operationId is already bound to another native-project request"
            )
        supplied = {
            "operationId": operation_id,
            "projectName": spec.project_name,
            "workspaceParent": str(raw_parent.resolve()),
            "plcSeries": spec.plc_series,
            "cpuModel": spec.cpu_model,
            "programName": spec.program_name,
            "language": spec.language,
            "description": spec.description,
            "xg5000Pid": spec.xg5000_pid,
            "xg5000Hwnd": spec.xg5000_hwnd,
        }
        if spec.planning_conversation_id is not None:
            supplied["reviewedPlan"] = self._reviewed_plan_payload(spec)
        stored = dict(existing.request)
        stored.pop("destinationDirectory", None)
        if stored != supplied or existing.actor_id != actor_id:
            raise NativeProjectConflict(
                "operationId is already bound to another actor or request"
            )
        return existing

    async def create(
        self,
        spec: NativeProjectSpec,
        *,
        actor_id: str,
    ) -> tuple[NativeProjectOperation, bool]:
        canonical = self._canonicalize_local_request(spec)
        destination = self._destination_directory(canonical)
        request_payload = self._request_payload(canonical, destination)
        request_digest = hashlib.sha256(
            _json_dump(request_payload).encode("utf-8")
        ).hexdigest()
        existing = self._repository.get(canonical.operation_id)
        if existing is not None:
            if (
                existing.request_digest != request_digest
                or existing.actor_id != actor_id
            ):
                raise NativeProjectConflict(
                    "operationId is already bound to another actor or request"
                )
            return existing, False

        await self._verify_exact_instance(canonical)
        operation, created = self._repository.allocate(
            operation_id=canonical.operation_id,
            request_digest=request_digest,
            actor_id=actor_id,
            request=request_payload,
            destination_directory=str(destination),
        )
        if not created and operation.state != "ALLOCATED":
            return operation, False

        try:
            self._ensure_allocated_directory(destination, allow_existing=not created)
            native_request = _validate_native_project_request(
                _NativeProjectRequest(
                    project_name=canonical.project_name,
                    destination_directory=destination,
                    plc_series=canonical.plc_series,
                    cpu_type=canonical.cpu_model,
                    program_name=canonical.program_name,
                    program_language=canonical.language,
                    description=canonical.description,
                )
            )
            plan_digest = _native_project_plan_digest(
                native_request,
                pid=canonical.xg5000_pid,
                hwnd=canonical.xg5000_hwnd,
            )
            operation = self._repository.set_plan_digest(
                canonical.operation_id,
                plan_digest,
            )
        except Exception as exc:
            current = self._repository.get(canonical.operation_id)
            if current is None:
                raise NativeProjectConflict(
                    "native project operation disappeared during preparation"
                ) from exc
            if current.state == "ALLOCATED":
                try:
                    current = self._repository.fail_allocated(
                        canonical.operation_id,
                        error_code="NATIVE_PROJECT_PREPARATION_FAILED",
                        error_detail=str(exc),
                    )
                except NativeProjectConflict:
                    # A concurrent idempotent request may have atomically begun
                    # execution after this request observed ALLOCATED.
                    current = self._repository.get(canonical.operation_id)
                    if current is None:
                        raise
            return current, created

        if not self._repository.begin_execution(canonical.operation_id):
            current = self._repository.get(canonical.operation_id)
            if current is None:
                raise NativeProjectConflict("native project operation disappeared")
            return current, False

        authorization = self._authorization(
            actor_id=actor_id,
            plan_digest=plan_digest,
        )
        arguments = {
            "pid": canonical.xg5000_pid,
            "hwnd": canonical.xg5000_hwnd,
            "destination_directory": str(destination),
            "project_name": canonical.project_name,
            "plc_series": canonical.plc_series,
            "cpu_type": canonical.cpu_model,
            "program_name": canonical.program_name,
            "program_language": canonical.language,
            "description": canonical.description,
            "timeout_seconds": 60.0,
            "authorization": authorization,
        }
        try:
            raw = await self._gateway.call_workspace_tool(
                "project.create_native",
                arguments,
            )
            result = self._validate_creation_result(
                raw,
                spec=canonical,
                destination=destination,
                plan_digest=plan_digest,
                authorization=authorization,
            )
        except Exception as exc:  # noqa: BLE001 - any post-dispatch fault is uncertain
            operation = self._repository.quarantine(
                canonical.operation_id,
                error_code=(
                    "NATIVE_PROJECT_MCP_REJECTED"
                    if isinstance(exc, MCPToolError)
                    else "NATIVE_PROJECT_EVIDENCE_UNVERIFIED"
                ),
                error_detail=str(exc),
            )
            return operation, created

        registration = await self._capture_workspace(
            result["projectPath"],
            result["projectSha256"],
        )
        result["workspaceRegistration"] = registration
        result["workspaceBound"] = False
        result["workspaceBindingReason"] = (
            "XG5000 is displaying the newly created source artifact while the "
            "captured workspace uses a separate working copy; exact working-copy "
            "identity was not proven, so Host did not bind it as active."
        )
        operation = self._repository.complete(canonical.operation_id, result)
        return operation, created

    async def reconcile(
        self,
        operation_id: str,
        evidence: NativeProjectRecoveryEvidence,
    ) -> NativeProjectOperation:
        """Resolve a quarantined create only from current read-only evidence."""

        operation = self._repository.get(operation_id)
        if operation is None:
            raise NativeProjectConflict("native project operation not found")
        if operation.state != "QUARANTINED":
            raise NativeProjectConflict(
                "only a quarantined native project operation can be reconciled"
            )
        request = operation.request
        expected_pid = int(request["xg5000Pid"])
        expected_hwnd = int(request["xg5000Hwnd"])
        destination = Path(operation.destination_directory).resolve()
        expected_path = (
            destination / f"{request['projectName']!s}.xgwx"
        ).resolve()
        supplied_path = Path(evidence.expected_project_path).resolve()
        recovery_evidence: dict[str, Any] = {
            "schema": "controlstudio-native-project-recovery/v1",
            "operationId": operation.operation_id,
            "requestDigest": operation.request_digest,
            "planDigest": operation.plan_digest,
            "expectedSpec": {
                key: request[key]
                for key in (
                    "projectName",
                    "plcSeries",
                    "cpuModel",
                    "programName",
                    "language",
                    "description",
                )
            },
            "expectedProjectPath": str(expected_path),
            "expectedProjectSha256": evidence.expected_project_sha256,
            "pid": evidence.pid,
            "hwnd": evidence.hwnd,
        }
        if (
            evidence.pid != expected_pid
            or evidence.hwnd != expected_hwnd
            or _canonical_path(supplied_path) != _canonical_path(expected_path)
        ):
            return self._repository.resolve_quarantine(
                operation_id,
                state="QUARANTINED",
                result={"recovery": recovery_evidence},
                error_code="NATIVE_PROJECT_RECOVERY_IDENTITY_MISMATCH",
                error_detail=(
                    "Recovery PID/HWND/path did not exactly match the stored "
                    "native-project contract."
                ),
            )

        try:
            await self._verify_exact_instance(
                NativeProjectSpec(
                    operation_id=operation.operation_id,
                    project_name=str(request["projectName"]),
                    workspace_parent=Path(str(request["workspaceParent"])),
                    plc_series=str(request["plcSeries"]),
                    cpu_model=str(request["cpuModel"]),
                    program_name=str(request["programName"]),
                    language=str(request["language"]),
                    description=str(request["description"]),
                    xg5000_pid=expected_pid,
                    xg5000_hwnd=expected_hwnd,
                )
            )
        except (
            MCPToolError,
            NativeProjectPreconditionError,
            OSError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            recovery_evidence["instanceError"] = str(exc)
            return self._repository.resolve_quarantine(
                operation_id,
                state="QUARANTINED",
                result={"recovery": recovery_evidence},
                error_code="NATIVE_PROJECT_RECOVERY_TARGET_UNVERIFIED",
                error_detail="The exact XG5000 PID/HWND could not be reverified.",
            )

        if expected_path.is_file() and not expected_path.is_symlink():
            actual_hash = _sha256_file(expected_path)
            recovery_evidence["observedProjectSha256"] = actual_hash
            if (
                evidence.expected_project_sha256 is not None
                and actual_hash != evidence.expected_project_sha256.casefold()
            ):
                return self._repository.resolve_quarantine(
                    operation_id,
                    state="QUARANTINED",
                    result={"recovery": recovery_evidence},
                    error_code="NATIVE_PROJECT_RECOVERY_HASH_MISMATCH",
                    error_detail=(
                        "The native artifact hash did not match the explicit "
                        "recovery evidence."
                    ),
                )
            try:
                attached = await self._gateway.call_read_tool(
                    "xg5000.attach",
                    {
                        "pid": expected_pid,
                        "hwnd": expected_hwnd,
                        "project_path": str(expected_path),
                        "project_hash": actual_hash,
                        "access_profile": "analysis",
                    },
                )
                session = (
                    attached.get("session")
                    if isinstance(attached, dict)
                    and isinstance(attached.get("session"), dict)
                    else attached
                )
                if (
                    not isinstance(session, dict)
                    or session.get("pid") != expected_pid
                    or session.get("hwnd") != expected_hwnd
                    or _canonical_path(Path(str(session.get("project_path") or "")))
                    != _canonical_path(expected_path)
                    or str(session.get("project_hash") or "").casefold()
                    != actual_hash
                    or not isinstance(session.get("metadata"), dict)
                    or session["metadata"].get(
                        "project_identity_verified_against_selected_pid"
                    )
                    is not True
                ):
                    raise NativeProjectEvidenceError(
                        "attach did not prove the exact project artifact"
                    )
                self._gateway.bind_agent_session(session)
                try:
                    catalog = await self._gateway.call_read_tool(
                        "project.list_programs",
                        {"name_filter": str(request["programName"])},
                    )
                finally:
                    self._gateway.clear_agent_session()
                programs = (
                    catalog.get("programs") if isinstance(catalog, dict) else None
                )
                exact_programs = [
                    item
                    for item in programs or []
                    if isinstance(item, dict)
                    and item.get("name") == request["programName"]
                    and str(item.get("language") or "").casefold()
                    == str(request["language"]).casefold()
                ]
                if len(exact_programs) != 1:
                    raise NativeProjectEvidenceError(
                        "the requested program/language was not uniquely verified"
                    )
            except (
                MCPToolError,
                NativeProjectEvidenceError,
                OSError,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                recovery_evidence["projectProbeError"] = str(exc)
                return self._repository.resolve_quarantine(
                    operation_id,
                    state="QUARANTINED",
                    result={"recovery": recovery_evidence},
                    error_code="NATIVE_PROJECT_RECOVERY_PROJECT_UNVERIFIED",
                    error_detail=(
                        "The artifact exists, but current XG5000 project/spec "
                        "identity was not proven."
                    ),
                )
            recovery_evidence["session"] = session
            recovery_evidence["program"] = exact_programs[0]
            return self._repository.resolve_quarantine(
                operation_id,
                state="COMPLETED",
                result={
                    "recovery": recovery_evidence,
                    "projectPath": str(expected_path),
                    "projectSha256": actual_hash,
                    "identityVerified": True,
                    "controllerConnected": False,
                    "plcWritePerformed": False,
                },
                error_code=None,
                error_detail=None,
            )

        # Absence alone is not enough. FAILED is safe only when the exact
        # selected XG5000 window proves that a different project is open and
        # the allocated destination is still empty.
        try:
            destination_empty = destination.is_dir() and not any(destination.iterdir())
            attached = await self._gateway.call_read_tool(
                "xg5000.attach",
                {
                    "pid": expected_pid,
                    "hwnd": expected_hwnd,
                    "access_profile": "analysis",
                },
            )
            session = (
                attached.get("session")
                if isinstance(attached, dict)
                and isinstance(attached.get("session"), dict)
                else attached
            )
            observed_path = Path(
                str(session.get("project_path") or "")
                if isinstance(session, dict)
                else ""
            ).resolve()
            different_project = (
                isinstance(session, dict)
                and session.get("pid") == expected_pid
                and session.get("hwnd") == expected_hwnd
                and isinstance(session.get("metadata"), dict)
                and session["metadata"].get(
                    "project_identity_verified_against_selected_pid"
                )
                is True
                and observed_path.is_file()
                and _canonical_path(observed_path) != _canonical_path(expected_path)
            )
        except (
            MCPToolError,
            OSError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            recovery_evidence["absenceProbeError"] = str(exc)
            destination_empty = False
            different_project = False
        if destination_empty and different_project:
            recovery_evidence["observedDifferentProject"] = str(observed_path)
            return self._repository.resolve_quarantine(
                operation_id,
                state="FAILED",
                result={"recovery": recovery_evidence},
                error_code="NATIVE_PROJECT_CONFIRMED_NOT_CREATED",
                error_detail=(
                    "The allocated destination is empty and the exact XG5000 "
                    "window proves a different project is open."
                ),
            )
        return self._repository.resolve_quarantine(
            operation_id,
            state="QUARANTINED",
            result={"recovery": recovery_evidence},
            error_code="NATIVE_PROJECT_RECOVERY_OUTCOME_UNCERTAIN",
            error_detail=(
                "The current file/window evidence cannot safely prove creation "
                "or non-creation; the target remains locked."
            ),
        )

    @staticmethod
    def _canonicalize_local_request(spec: NativeProjectSpec) -> NativeProjectSpec:
        operation_id = _canonical_uuid(spec.operation_id, "operationId")
        raw_parent = Path(spec.workspace_parent).expanduser()
        if not raw_parent.is_absolute():
            raise NativeProjectPreconditionError("workspaceParent must be absolute")
        if raw_parent.is_symlink():
            raise NativeProjectPreconditionError(
                "workspaceParent must not be a symlink"
            )
        try:
            parent = raw_parent.resolve(strict=True)
        except OSError as exc:
            raise NativeProjectPreconditionError(
                "workspaceParent must be an existing absolute directory"
            ) from exc
        if not parent.is_dir():
            raise NativeProjectPreconditionError(
                "workspaceParent must be a real directory, not a file or symlink"
            )
        return NativeProjectSpec(
            operation_id=operation_id,
            project_name=spec.project_name,
            workspace_parent=parent,
            plc_series=spec.plc_series,
            cpu_model=spec.cpu_model,
            program_name=spec.program_name,
            language=spec.language,
            description=spec.description,
            xg5000_pid=spec.xg5000_pid,
            xg5000_hwnd=spec.xg5000_hwnd,
            planning_conversation_id=spec.planning_conversation_id,
            planning_message_id=spec.planning_message_id,
            planning_plan_digest=spec.planning_plan_digest,
            reviewed_section_ids=tuple(spec.reviewed_section_ids),
        )

    async def _verify_exact_instance(self, spec: NativeProjectSpec) -> None:
        try:
            raw_instances = await self._gateway.call_read_tool(
                "xg5000.instances",
                {},
            )
        except MCPToolError as exc:
            raise NativeProjectPreconditionError(str(exc)) from exc
        if not isinstance(raw_instances, dict) or not isinstance(
            raw_instances.get("instances"),
            list,
        ):
            raise NativeProjectPreconditionError(
                "MCP returned an invalid XG5000 instance list"
            )
        matches = [
            item
            for item in raw_instances["instances"]
            if isinstance(item, dict)
            and item.get("pid") == spec.xg5000_pid
            and item.get("hwnd") == spec.xg5000_hwnd
        ]
        if len(matches) != 1:
            raise NativeProjectPreconditionError(
                "the exact xg5000Pid/xg5000Hwnd pair did not resolve to one instance"
            )
        instance = matches[0]
        if instance.get("visible") is not True:
            raise NativeProjectPreconditionError(
                "the selected XG5000 main window is not visible"
            )

    @staticmethod
    def _destination_directory(spec: NativeProjectSpec) -> Path:
        operation_suffix = uuid.UUID(spec.operation_id).hex[:12]
        return spec.workspace_parent / f"{spec.project_name}-{operation_suffix}"

    @staticmethod
    def _request_payload(
        spec: NativeProjectSpec,
        destination: Path,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "operationId": spec.operation_id,
            "projectName": spec.project_name,
            "workspaceParent": str(spec.workspace_parent),
            "destinationDirectory": str(destination),
            "plcSeries": spec.plc_series,
            "cpuModel": spec.cpu_model,
            "programName": spec.program_name,
            "language": spec.language,
            "description": spec.description,
            "xg5000Pid": spec.xg5000_pid,
            "xg5000Hwnd": spec.xg5000_hwnd,
        }
        if spec.planning_conversation_id is not None:
            payload["reviewedPlan"] = NativeProjectService._reviewed_plan_payload(
                spec
            )
        return payload

    @staticmethod
    def _reviewed_plan_payload(spec: NativeProjectSpec) -> dict[str, Any]:
        values = (
            spec.planning_conversation_id,
            spec.planning_message_id,
            spec.planning_plan_digest,
        )
        if not all(isinstance(value, str) and value for value in values):
            raise NativeProjectPreconditionError(
                "reviewed project plan identity must be complete"
            )
        if not spec.reviewed_section_ids:
            raise NativeProjectPreconditionError(
                "reviewed project plan section scope must not be empty"
            )
        return {
            "conversationId": spec.planning_conversation_id,
            "messageId": spec.planning_message_id,
            "planDigest": spec.planning_plan_digest,
            "reviewedSectionIds": list(spec.reviewed_section_ids),
        }

    @staticmethod
    def _ensure_allocated_directory(
        destination: Path,
        *,
        allow_existing: bool,
    ) -> None:
        parent = destination.parent.resolve(strict=True)
        _require_within(destination, parent, "destination_directory")
        if destination.exists():
            if (
                not allow_existing
                or not destination.is_dir()
                or destination.is_symlink()
                or any(destination.iterdir())
            ):
                raise NativeProjectPreconditionError(
                    "the allocated native-project directory is not a unique empty directory"
                )
            return
        destination.mkdir(mode=0o700, parents=False, exist_ok=False)
        if destination.is_symlink() or any(destination.iterdir()):
            raise NativeProjectPreconditionError(
                "the allocated native-project directory is not empty"
            )

    def _authorization(
        self,
        *,
        actor_id: str,
        plan_digest: str,
    ) -> dict[str, Any]:
        issued_at = _utc_now()
        claims = {
            "schema": NATIVE_PROJECT_AUTHORIZATION_SCHEMA,
            "authorization_id": str(uuid.uuid4()),
            "actor_id": actor_id,
            "plan_digest": plan_digest,
            "capability": "workspace_write",
            "allowed_operation": "project.create_native",
            "issued_at": issued_at.isoformat(timespec="milliseconds"),
            "expires_at": (issued_at + timedelta(minutes=2)).isoformat(
                timespec="milliseconds"
            ),
            "nonce": str(uuid.uuid4()),
        }
        return self._gateway.sign_mutation_authorization(claims)

    @staticmethod
    def _validate_creation_result(
        raw: Any,
        *,
        spec: NativeProjectSpec,
        destination: Path,
        plan_digest: str,
        authorization: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise NativeProjectEvidenceError("native project result is not an object")
        if (
            raw.get("ok") is not True
            or raw.get("verified") is not True
            or raw.get("operation") != "project.create_native"
            or raw.get("pid") != spec.xg5000_pid
            or raw.get("hwnd") != spec.xg5000_hwnd
        ):
            raise NativeProjectEvidenceError(
                "MCP did not verify the exact native project operation and XG5000 target"
            )
        data = raw.get("data")
        if not isinstance(data, dict):
            raise NativeProjectEvidenceError("native project evidence data is missing")
        expected_values = {
            "project_name": spec.project_name,
            "plc_series": spec.plc_series,
            "cpu_type": spec.cpu_model,
            "program_name": spec.program_name,
            "program_language": spec.language,
            "description": spec.description,
        }
        mismatches = {
            key: {"expected": expected, "observed": data.get(key)}
            for key, expected in expected_values.items()
            if data.get(key) != expected
        }
        if mismatches:
            raise NativeProjectEvidenceError(
                f"MCP returned substituted native-project fields: {sorted(mismatches)}"
            )
        if (
            _canonical_path(Path(str(data.get("destination_directory") or "")))
            != _canonical_path(destination)
            or data.get("plan_digest") != plan_digest
            or data.get("controller_connected") is not False
            or data.get("plc_write_performed") is not False
        ):
            raise NativeProjectEvidenceError(
                "native project result violated destination, plan, or no-PLC guarantees"
            )
        approval = data.get("authorization")
        if (
            not isinstance(approval, dict)
            or approval.get("authorization_id")
            != authorization.get("authorization_id")
            or approval.get("nonce") != authorization.get("nonce")
            or approval.get("actor_id") != authorization.get("actor_id")
            or approval.get("plan_digest") != plan_digest
            or approval.get("signature_verified") is not True
            or approval.get("consumed") is not True
        ):
            raise NativeProjectEvidenceError(
                "MCP did not prove consumption of the exact Host authorization"
            )

        project_path = Path(str(data.get("project_path") or "")).resolve()
        _require_within(project_path, destination, "project_path")
        if (
            not project_path.is_file()
            or project_path.is_symlink()
            or project_path.suffix.casefold() != ".xgwx"
            or project_path.stem.casefold() != spec.project_name.casefold()
        ):
            raise NativeProjectEvidenceError(
                "the returned project path is not the requested native .xgwx artifact"
            )
        actual_size = project_path.stat().st_size
        actual_hash = _sha256_file(project_path)
        if (
            actual_size <= 0
            or data.get("project_size") != actual_size
            or data.get("project_sha256") != actual_hash
        ):
            raise NativeProjectEvidenceError(
                "native project file size or SHA-256 evidence does not match disk"
            )
        identity = data.get("identity")
        target = data.get("target")
        if (
            not isinstance(identity, dict)
            or identity.get("verified") is not True
            or _canonical_path(Path(str(identity.get("project_path") or "")))
            != _canonical_path(project_path)
            or not isinstance(target, dict)
            or target.get("owner_pid") != spec.xg5000_pid
            or target.get("project_open") is not False
        ):
            raise NativeProjectEvidenceError(
                "native project identity or idle-target evidence is incomplete"
            )
        return {
            "identityVerified": True,
            "operation": "project.create_native",
            "xg5000Pid": spec.xg5000_pid,
            "xg5000Hwnd": spec.xg5000_hwnd,
            "projectPath": str(project_path),
            "projectSha256": actual_hash,
            "projectSize": actual_size,
            "destinationDirectory": str(destination),
            "planDigest": plan_digest,
            "controllerConnected": False,
            "plcWritePerformed": False,
            "identityEvidence": identity,
        }

    async def _capture_workspace(
        self,
        project_path: str,
        project_hash: str,
    ) -> dict[str, Any]:
        try:
            raw = await self._gateway.call_workspace_tool(
                "workspace.open_native",
                {
                    "source_path": project_path,
                    "expected_source_hash": project_hash,
                },
            )
            workspace = self._validate_workspace_capture(
                raw,
                project_path=Path(project_path),
                project_hash=project_hash,
            )
        except Exception as exc:  # noqa: BLE001 - capture is best-effort after proof
            return {
                "state": "capture_failed",
                "detail": str(exc),
            }
        return {
            "state": "captured_not_bound",
            "detail": (
                "The new source artifact was captured into a protected workspace, "
                "but its separate working copy is not the project currently open in XG5000."
            ),
            "workspace": workspace,
        }

    @staticmethod
    def _validate_workspace_capture(
        raw: Any,
        *,
        project_path: Path,
        project_hash: str,
    ) -> dict[str, Any]:
        if (
            not isinstance(raw, dict)
            or raw.get("ok") is not True
            or raw.get("verified") is not True
        ):
            raise NativeProjectEvidenceError("workspace capture was not verified")
        _canonical_uuid(str(raw.get("workspace_id") or ""), "workspace_id")
        _canonical_uuid(str(raw.get("revision_id") or ""), "revision_id")
        source = raw.get("source")
        working = raw.get("working_copy")
        if not isinstance(source, dict) or not isinstance(working, dict):
            raise NativeProjectEvidenceError("workspace artifact identity is incomplete")
        if (
            _canonical_path(Path(str(source.get("original_path") or "")))
            != _canonical_path(project_path)
            or source.get("sha256") != project_hash
            or working.get("baseline_sha256") != project_hash
            or working.get("current_sha256") != project_hash
            or working.get("state") != "clean"
        ):
            raise NativeProjectEvidenceError(
                "workspace capture does not match the created native project"
            )
        for field, container in (
            ("captured_path", source),
            ("path", working),
        ):
            path = Path(str(container.get(field) or "")).resolve()
            if (
                not path.is_file()
                or path.is_symlink()
                or _sha256_file(path) != project_hash
            ):
                raise NativeProjectEvidenceError(
                    f"workspace {field} does not match the created project"
                )
        return raw


__all__ = [
    "NativeProjectConflict",
    "NativeProjectEvidenceError",
    "NativeProjectOperation",
    "NativeProjectPreconditionError",
    "NativeProjectRepository",
    "NativeProjectService",
    "NativeProjectSpec",
]
