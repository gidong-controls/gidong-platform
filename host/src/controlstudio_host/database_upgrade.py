from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .knowledge import KNOWLEDGE_SCHEMA_VERSION
from .store import HOST_SCHEMA_VERSION


class DatabaseUpgradeError(RuntimeError):
    """The authoritative database pair cannot be upgraded safely."""


_ROLES = ("host", "knowledge")
_SCHEMA_TABLES = {
    "host": "schema_migrations",
    "knowledge": "knowledge_schema",
}
_TARGET_VERSIONS = {
    "host": HOST_SCHEMA_VERSION,
    "knowledge": KNOWLEDGE_SCHEMA_VERSION,
}


def _read_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5.0)


def _schema_version(path: Path, table: str) -> int:
    if not path.exists():
        return 0
    with closing(_read_connection(path)) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if exists is None:
            return 0
        row = connection.execute(f'SELECT MAX(version) FROM "{table}"').fetchone()
    version = row[0] if row is not None else None
    if version is None:
        return 0
    if not isinstance(version, int) or version < 0:
        raise DatabaseUpgradeError(f"{table} contains an invalid schema version")
    return version


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_integrity(path: Path) -> None:
    with closing(_read_connection(path)) as connection:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    if row != ("ok",):
        raise DatabaseUpgradeError(f"SQLite integrity check failed for {path.name}")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(
                value,
                stream,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DatabaseUpgradeError(
            f"database recovery metadata is unreadable: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise DatabaseUpgradeError(f"database recovery metadata is invalid: {path}")
    return value


def _backup_database(source: Path, destination: Path) -> None:
    with closing(_read_connection(source)) as source_connection, closing(
        sqlite3.connect(destination)
    ) as destination_connection:
        source_connection.backup(destination_connection)
    _assert_integrity(destination)


def _create_snapshot(
    paths: Mapping[str, Path],
    versions: Mapping[str, int],
    backup_root: Path,
) -> tuple[str, Path]:
    operation_id = str(uuid.uuid4())
    snapshot = backup_root / operation_id
    snapshot.mkdir(parents=True)
    databases: dict[str, dict[str, Any]] = {}
    try:
        for role in _ROLES:
            source = paths[role]
            existed = source.is_file()
            entry: dict[str, Any] = {
                "existed": existed,
                "schemaVersion": versions[role],
                "target": str(source),
            }
            if existed:
                backup_path = snapshot / f"{role}.sqlite3"
                _backup_database(source, backup_path)
                entry["sha256"] = _sha256(backup_path)
            databases[role] = entry
        _write_json(
            snapshot / "manifest.json",
            {
                "schema": "controlstudio-database-upgrade-snapshot/v1",
                "operationId": operation_id,
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "databases": databases,
            },
        )
    except BaseException:
        shutil.rmtree(snapshot, ignore_errors=True)
        raise
    return operation_id, snapshot


def _remove_database(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        candidate.unlink(missing_ok=True)


def _restore_snapshot(snapshot: Path, paths: Mapping[str, Path]) -> None:
    manifest = _load_json(snapshot / "manifest.json")
    if (
        manifest.get("schema") != "controlstudio-database-upgrade-snapshot/v1"
        or manifest.get("operationId") != snapshot.name
        or not isinstance(manifest.get("databases"), dict)
    ):
        raise DatabaseUpgradeError("database recovery manifest is invalid")
    databases = manifest["databases"]
    if set(databases) != set(_ROLES):
        raise DatabaseUpgradeError("database recovery manifest roles are invalid")
    for role in _ROLES:
        entry = databases[role]
        target = paths[role]
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("existed"), bool)
            or entry.get("target") != str(target)
        ):
            raise DatabaseUpgradeError("database recovery entry is invalid")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not entry["existed"]:
            _remove_database(target)
            continue
        backup = snapshot / f"{role}.sqlite3"
        expected_hash = entry.get("sha256")
        if not isinstance(expected_hash, str) or _sha256(backup) != expected_hash:
            raise DatabaseUpgradeError(f"{role} database snapshot hash mismatched")
        _assert_integrity(backup)
        temporary = target.with_name(f".{target.name}.{snapshot.name}.restore")
        try:
            shutil.copy2(backup, temporary)
            _assert_integrity(temporary)
            _remove_database(target)
            os.replace(temporary, target)
            if _sha256(target) != expected_hash:
                raise DatabaseUpgradeError(f"{role} database restore hash mismatched")
            _assert_integrity(target)
        finally:
            temporary.unlink(missing_ok=True)


def _recover_pending(marker: Path, backup_root: Path, paths: Mapping[str, Path]) -> None:
    pending = _load_json(marker)
    operation_id = pending.get("operationId")
    try:
        canonical_id = str(uuid.UUID(operation_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise DatabaseUpgradeError("database recovery operation id is invalid") from exc
    if canonical_id != operation_id:
        raise DatabaseUpgradeError("database recovery operation id is not canonical")
    _restore_snapshot(backup_root / operation_id, paths)
    marker.unlink()


@contextmanager
def protect_database_upgrades(
    host_database: Path,
    knowledge_database: Path,
) -> Iterator[None]:
    paths = {
        "host": Path(host_database).expanduser().resolve(),
        "knowledge": Path(knowledge_database).expanduser().resolve(),
    }
    backup_root = paths["host"].parent / "migration-backups"
    marker = paths["host"].parent / "migration-recovery.json"
    if marker.exists():
        _recover_pending(marker, backup_root, paths)

    versions = {
        role: _schema_version(paths[role], _SCHEMA_TABLES[role]) for role in _ROLES
    }
    future = [role for role in _ROLES if versions[role] > _TARGET_VERSIONS[role]]
    if future:
        raise DatabaseUpgradeError(
            "database schema is newer than this Host: " + ", ".join(future)
        )
    if all(versions[role] == _TARGET_VERSIONS[role] for role in _ROLES):
        yield
        return

    operation_id, snapshot = _create_snapshot(paths, versions, backup_root)
    _write_json(marker, {"operationId": operation_id})
    try:
        yield
        migrated = {
            role: _schema_version(paths[role], _SCHEMA_TABLES[role]) for role in _ROLES
        }
        if migrated != _TARGET_VERSIONS:
            raise DatabaseUpgradeError(
                "database pair did not reach the expected schema versions"
            )
    except BaseException as exc:
        try:
            _restore_snapshot(snapshot, paths)
            marker.unlink()
        except BaseException as recovery_exc:
            raise DatabaseUpgradeError(
                f"database pair recovery is required; snapshot retained at {snapshot}"
            ) from recovery_exc
        exc.add_note(
            "ControlStudio restored the pre-upgrade Host and knowledge database pair."
        )
        raise
    else:
        marker.unlink()
