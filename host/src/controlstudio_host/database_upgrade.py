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
from urllib.parse import quote

from .knowledge import KNOWLEDGE_SCHEMA_VERSION
from .store import (
    HOST_SCHEMA_VERSION,
    apply_registered_atomic_additive_migration,
    registered_atomic_additive_migration,
)


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


def _filesystem_path(path: Path) -> Path:
    """Use Windows extended-length syntax only at filesystem I/O boundaries."""
    if os.name != "nt":
        return path
    absolute = os.path.abspath(os.fspath(path))
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def _read_connection(path: Path) -> sqlite3.Connection:
    uri = (
        "file:" + quote(os.fspath(_filesystem_path(path)), safe="")
        if os.name == "nt"
        else path.as_uri()
    )
    return sqlite3.connect(f"{uri}?mode=ro", uri=True, timeout=5.0)


def _schema_version(path: Path, table: str) -> int:
    if not _filesystem_path(path).exists():
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
    with _filesystem_path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_integrity(path: Path) -> None:
    with closing(_read_connection(path)) as connection:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    if row != ("ok",):
        raise DatabaseUpgradeError(f"SQLite integrity check failed for {path.name}")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path = _filesystem_path(path)
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
        _filesystem_path(temporary).unlink(missing_ok=True)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_filesystem_path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DatabaseUpgradeError(
            f"database recovery metadata is unreadable: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise DatabaseUpgradeError(f"database recovery metadata is invalid: {path}")
    return value


def _backup_database(source: Path, destination: Path) -> None:
    with closing(_read_connection(source)) as source_connection, closing(
        sqlite3.connect(_filesystem_path(destination))
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
    _filesystem_path(snapshot).mkdir(parents=True)
    databases: dict[str, dict[str, Any]] = {}
    try:
        for role in _ROLES:
            source = paths[role]
            existed = _filesystem_path(source).is_file()
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
        shutil.rmtree(_filesystem_path(snapshot), ignore_errors=True)
        raise
    return operation_id, snapshot


def _remove_database(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        _filesystem_path(candidate).unlink(missing_ok=True)


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
        _filesystem_path(target.parent).mkdir(parents=True, exist_ok=True)
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
            shutil.copy2(_filesystem_path(backup), _filesystem_path(temporary))
            _assert_integrity(temporary)
            _remove_database(target)
            os.replace(_filesystem_path(temporary), _filesystem_path(target))
            if _sha256(target) != expected_hash:
                raise DatabaseUpgradeError(f"{role} database restore hash mismatched")
            _assert_integrity(target)
        finally:
            _filesystem_path(temporary).unlink(missing_ok=True)


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
    _filesystem_path(marker).unlink()


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
    if _filesystem_path(marker).exists():
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

    if (versions["knowledge"] == _TARGET_VERSIONS["knowledge"]
            and registered_atomic_additive_migration(versions["host"], _TARGET_VERSIONS["host"]) is not None):
        # This exact registered nullable addition preserves every old column and
        # row. SQLite journals only changed pages, not a full copy of Host data.
        # Never create a missing DB or borrow the pair recovery marker here.
        uri = ("file:" + quote(os.fspath(_filesystem_path(paths["host"])), safe="")
               if os.name == "nt" else paths["host"].as_uri())
        with closing(sqlite3.connect(f"{uri}?mode=rw", uri=True, timeout=5.0)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            apply_registered_atomic_additive_migration(
                connection, versions["host"], _TARGET_VERSIONS["host"])
        # Commit precedes normal store initialization on its own connections.
        # A later startup/seed failure leaves the valid additive29 schema and
        # preserved data; retry sees29 and does not replay the migration. This
        # is not the destructive-pair rollback protocol and has no inverse DDL.
        yield
        migrated = {role: _schema_version(paths[role], _SCHEMA_TABLES[role]) for role in _ROLES}
        if migrated != _TARGET_VERSIONS:
            raise DatabaseUpgradeError("Registered additive upgrade did not retain expected versions")
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
            _filesystem_path(marker).unlink()
        except BaseException as recovery_exc:
            raise DatabaseUpgradeError(
                f"database pair recovery is required; snapshot retained at {snapshot}"
            ) from recovery_exc
        exc.add_note(
            "ControlStudio restored the pre-upgrade Host and knowledge database pair."
        )
        raise
    else:
        _filesystem_path(marker).unlink()
