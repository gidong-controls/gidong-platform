from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import quote


class KnowledgeError(RuntimeError):
    """Base error for the local engineering knowledge repository."""


class KnowledgeConflict(KnowledgeError):
    """A requested state transition conflicts with the current repository state."""


class KnowledgeUnavailable(KnowledgeError):
    """The local SQLite runtime cannot provide the required knowledge capability."""


class KnowledgeKind(str, Enum):
    MANUAL = "manual"
    STANDARD = "standard"
    PROJECT_DOCUMENT = "project_document"
    MEMORY = "memory"
    PREFERENCE = "preference"
    CONTROL_PATTERN = "control_pattern"


class KnowledgeStatus(str, Enum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class PageRecord:
    text: str | bytes
    page: int | None = None
    locator: str | None = None
    section: str | None = None
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class KnowledgeSource:
    source_id: str
    kind: KnowledgeKind
    title: str
    scope: str
    vendor: str | None
    product_family: str | None
    version: str | None
    language: str | None
    authority: str
    status: KnowledgeStatus
    sha256: str
    locator: str | None
    revision: str
    supersedes: str | None
    metadata: dict[str, Any]
    external_key: str | None
    confirmed_by: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ControlPatternSeed:
    pattern_id: str
    title: str
    content: str
    scope: str = "global"
    vendor: str | None = None
    product_family: str | None = None
    version: str | None = None
    language: str = "en"
    authority: str = "builtin-reviewed"
    revision: str = "1"
    metadata: Mapping[str, Any] | None = None


KNOWLEDGE_SCHEMA_VERSION = 2
_MAX_SOURCE_CHARS = 25_000_000
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REDACTED = "[REDACTED]"
_SECRET_TOKEN_PATTERN = re.compile(r"(?i)\b(?:sk|rk|pk)-[A-Za-z0-9_-]{8,}\b")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?im)(\b(?:api[_ -]?key|password|passwd|secret|"
    r"access[_ -]?token|refresh[_ -]?token|authorization)"
    r"\b\s*[\"']?\s*[:=]\s*[\"']?)([^\s,\"';]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_URI_CREDENTIAL_PATTERN = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?"
    r"-----END(?: [A-Z0-9]+)? PRIVATE KEY-----",
    flags=re.DOTALL,
)
_SENSITIVE_METADATA_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "credentials",
        "password",
        "passwd",
        "refresh_token",
        "secret",
        "token",
        "access_token",
    }
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_dump(value: Mapping[str, Any] | Sequence[Any]) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("knowledge metadata must be JSON serializable") from exc


def _json_object(value: str) -> dict[str, Any]:
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise KnowledgeError("stored knowledge metadata is not an object")
    return loaded


def _decode_text(value: str | bytes, field: str) -> str:
    if isinstance(value, bytes):
        try:
            decoded = value.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{field} bytes must be UTF-8 encoded") from exc
    elif isinstance(value, str):
        decoded = value
    else:
        raise TypeError(f"{field} must be str or bytes")
    decoded = decoded.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not decoded:
        raise ValueError(f"{field} must not be empty")
    if "\x00" in decoded:
        raise ValueError(f"{field} must not contain NUL characters")
    return decoded


def _first_heading(value: str) -> str | None:
    """Keep a normalized document heading with the Platform source payload."""

    for line in value.splitlines():
        candidate = line.strip()
        if candidate.startswith("#"):
            heading = candidate.lstrip("#").strip()
            return heading or None
    return None


def _redact_sensitive_text(value: str) -> str:
    redacted = _PRIVATE_KEY_PATTERN.sub(_REDACTED, value)
    redacted = _BEARER_PATTERN.sub(f"Bearer {_REDACTED}", redacted)
    redacted = _URI_CREDENTIAL_PATTERN.sub(rf"\1{_REDACTED}@", redacted)
    redacted = _SECRET_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(1)}{_REDACTED}",
        redacted,
    )
    # A local import can contain malformed UTF-16 from a producer-specific
    # extraction.  The sidecar protocol is UTF-8 NDJSON, so never let an
    # unpaired surrogate escape the explicit sync boundary.
    return _SECRET_TOKEN_PATTERN.sub(_REDACTED, redacted).encode(
        "utf-8", "replace"
    ).decode("utf-8")


def _sanitize_json_value(value: Any, *, key: str | None = None, depth: int = 0) -> Any:
    if depth > 20:
        raise ValueError("knowledge metadata nesting exceeds 20 levels")
    normalized_key = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_") if key else None
    if normalized_key in _SENSITIVE_METADATA_KEYS:
        return _REDACTED
    if isinstance(value, str):
        return _redact_sensitive_text(value)
    if isinstance(value, Mapping):
        return {
            str(child_key): _sanitize_json_value(
                child_value,
                key=str(child_key),
                depth=depth + 1,
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_json_value(child, depth=depth + 1) for child in value]
    return value


def _clean_required(value: str, field: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum or "\x00" in cleaned:
        raise ValueError(f"{field} must contain between 1 and {maximum} safe characters")
    return cleaned


def _clean_optional(
    value: str | None,
    field: str,
    *,
    maximum: int = 1_024,
) -> str | None:
    if value is None:
        return None
    cleaned = _clean_required(value, field, maximum=maximum)
    return cleaned


def _metadata(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("metadata must be a mapping")
    copied = _sanitize_json_value(dict(value))
    if not isinstance(copied, dict):  # Mapping input guarantees this.
        raise TypeError("metadata must be an object")
    encoded = _json_dump(copied)
    if len(encoded.encode("utf-8")) > 4 * 1024 * 1024:
        raise ValueError("knowledge metadata exceeds the 4 MB limit")
    return copied


def _kind(value: KnowledgeKind | str) -> KnowledgeKind:
    try:
        return KnowledgeKind(value)
    except ValueError as exc:
        raise ValueError(f"unsupported knowledge kind: {value}") from exc


def _status(value: KnowledgeStatus | str) -> KnowledgeStatus:
    try:
        return KnowledgeStatus(value)
    except ValueError as exc:
        raise ValueError(f"unsupported knowledge status: {value}") from exc


def _safe_sha256(value: str | None, payload: bytes) -> str:
    if value is None:
        return hashlib.sha256(payload).hexdigest()
    normalized = value.strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise ValueError("source_sha256 must be 64 lowercase hexadecimal characters")
    return normalized


def _sanitize_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    """Redact metadata only at the explicit Platform-to-Agent boundary."""

    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        if key.casefold() in _SENSITIVE_METADATA_KEYS:
            continue
        if isinstance(item, str):
            sanitized[key] = _redact_sensitive_text(item)
        elif isinstance(item, Mapping):
            sanitized[key] = _sanitize_metadata(item)
        elif isinstance(item, list):
            sanitized[key] = [
                _redact_sensitive_text(value) if isinstance(value, str) else value
                for value in item
            ]
        else:
            sanitized[key] = item
    return sanitized


def _normalize_vendor(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "", value.strip().casefold())
    aliases = {
        "ls": "ls",
        "lselectric": "ls",
        "lselectrics": "ls",
        "mitsubishi": "mitsubishi",
        "mitsubishielectric": "mitsubishi",
        "rockwell": "rockwell",
        "rockwellautomation": "rockwell",
        "allenbradley": "rockwell",
        "ab": "rockwell",
        "generic": "generic",
        "vendorneutral": "generic",
    }
    return aliases.get(normalized, normalized)


class KnowledgeStore:
    """Platform authority store for source documents and their lifecycle.

    Retrieval is intentionally absent from this class.  The Platform retains
    the durable, normalized source payload and transfers an explicitly
    sanitized snapshot to the private Agent, where chunking, indexing and
    ranking are disposable sidecar concerns.
    """

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
        # v1 indexed chunks locally.  v2 migrates those records once into
        # durable source/page payloads; Agent owns all later chunking.
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            try:
                with connection:
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS knowledge_schema (
                            version INTEGER PRIMARY KEY,
                            applied_at TEXT NOT NULL
                        );

                        CREATE TABLE IF NOT EXISTS knowledge_sources (
                            source_id TEXT PRIMARY KEY,
                            external_key TEXT UNIQUE,
                            kind TEXT NOT NULL CHECK (
                                kind IN (
                                    'manual', 'standard', 'project_document',
                                    'memory', 'preference', 'control_pattern'
                                )
                            ),
                            title TEXT NOT NULL,
                            scope TEXT NOT NULL,
                            vendor TEXT,
                            vendor_key TEXT,
                            product_family TEXT,
                            version TEXT,
                            language TEXT,
                            authority TEXT NOT NULL,
                            status TEXT NOT NULL CHECK (
                                status IN (
                                    'proposed', 'confirmed',
                                    'superseded', 'archived'
                                )
                            ),
                            sha256 TEXT NOT NULL,
                            locator TEXT,
                            revision TEXT NOT NULL,
                            supersedes TEXT REFERENCES knowledge_sources(source_id),
                            metadata_json TEXT NOT NULL,
                            confirmed_by TEXT,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );

                        CREATE INDEX IF NOT EXISTS idx_knowledge_source_filters
                        ON knowledge_sources(status, kind, scope, vendor_key);

                        CREATE INDEX IF NOT EXISTS idx_knowledge_source_hash
                        ON knowledge_sources(sha256);

                        CREATE TABLE IF NOT EXISTS knowledge_source_payloads (
                            source_id TEXT PRIMARY KEY
                                REFERENCES knowledge_sources(source_id)
                                ON DELETE RESTRICT,
                            payload_format TEXT NOT NULL CHECK (
                                payload_format IN (
                                    'text', 'pages-json',
                                    'legacy-chunk-reconstruction'
                                )
                            ),
                            payload BLOB NOT NULL,
                            payload_sha256 TEXT NOT NULL,
                            created_at TEXT NOT NULL
                        );

                        """
                    )
                    self._migrate_legacy_chunks_to_source_payloads(connection)
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO knowledge_schema(version, applied_at)
                        VALUES (?, ?)
                        """,
                        (KNOWLEDGE_SCHEMA_VERSION, _utc_text(_utc_now())),
                    )
            finally:
                # Transaction context already commits/rolls back; this keeps
                # initialization errors transparent to the caller.
                pass
            version_row = connection.execute(
                "SELECT MAX(version) AS version FROM knowledge_schema"
            ).fetchone()
            version = int(version_row["version"] or 0)
            if version > KNOWLEDGE_SCHEMA_VERSION:
                raise KnowledgeUnavailable(
                    f"knowledge database schema {version} is newer than supported "
                    f"schema {KNOWLEDGE_SCHEMA_VERSION}"
                )

    def ping(self) -> bool:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT count(*) AS count FROM knowledge_sources"
                ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    @staticmethod
    def _migrate_legacy_chunks_to_source_payloads(
        connection: sqlite3.Connection,
    ) -> None:
        """Migrate pre-v2 derived Platform chunks into durable source records.

        Legacy databases did not retain source/page payloads.  Their chunk
        sequence is therefore preserved as a labelled normalized extraction;
        new imports are lossless text/page payloads.  The retired Platform
        cache and search audit tables are dropped after this one-way migration.
        """

        chunks_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'knowledge_chunks'"
        ).fetchone()
        if chunks_table is not None:
            missing = connection.execute(
                """
                SELECT s.source_id
                FROM knowledge_sources AS s
                LEFT JOIN knowledge_source_payloads AS p ON p.source_id = s.source_id
                WHERE p.source_id IS NULL
                """
            ).fetchall()
            for row in missing:
                source_id = str(row["source_id"])
                chunks = connection.execute(
                    """
                    SELECT content, page, locator, section
                    FROM knowledge_chunks
                    WHERE source_id = ?
                    ORDER BY ordinal
                    """,
                    (source_id,),
                ).fetchall()
                pages = [
                    {
                        "text": str(chunk["content"]),
                        "page": chunk["page"],
                        "locator": chunk["locator"],
                        "section": chunk["section"],
                    }
                    for chunk in chunks
                ]
                if not pages:
                    continue
                payload = _json_dump(pages).encode("utf-8")
                connection.execute(
                    """
                    INSERT INTO knowledge_source_payloads(
                        source_id, payload_format, payload, payload_sha256, created_at
                    ) VALUES (?, 'legacy-chunk-reconstruction', ?, ?, ?)
                    """,
                    (
                        source_id,
                        payload,
                        hashlib.sha256(payload).hexdigest(),
                        _utc_text(_utc_now()),
                    ),
                )
            connection.execute("DROP TABLE IF EXISTS knowledge_fts_word")
            connection.execute("DROP TABLE IF EXISTS knowledge_fts_grams")
            connection.execute("DROP TRIGGER IF EXISTS knowledge_chunks_after_delete")
            connection.execute("DROP TABLE IF EXISTS knowledge_chunks")
        connection.execute("DROP TABLE IF EXISTS knowledge_search_audit")

    def summary(self) -> dict[str, int | str]:
        """Return small product-facing counts without exposing raw index tables."""

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT
                    SUM(
                        CASE WHEN status IN ('proposed', 'confirmed')
                        THEN 1 ELSE 0 END
                    ) AS source_count,
                    SUM(
                        CASE WHEN kind = 'manual'
                         AND status IN ('proposed', 'confirmed')
                        THEN 1 ELSE 0 END
                    )
                        AS manual_count,
                    SUM(
                        CASE WHEN kind = 'control_pattern'
                         AND status IN ('proposed', 'confirmed')
                        THEN 1 ELSE 0 END
                    )
                        AS pattern_count,
                    SUM(
                        CASE
                            WHEN kind IN ('memory', 'preference')
                             AND status = 'confirmed'
                            THEN 1 ELSE 0
                        END
                    ) AS confirmed_memory_count,
                    SUM(
                        CASE
                            WHEN kind IN ('memory', 'preference')
                             AND status = 'proposed'
                            THEN 1 ELSE 0
                        END
                    ) AS proposed_memory_count
                FROM knowledge_sources
                """
            ).fetchone()
        return {
            "source_count": int(row["source_count"] or 0),
            # Chunk count is intentionally not knowable to Platform: it is a
            # volatile Agent implementation detail.
            "chunk_count": 0,
            "manual_count": int(row["manual_count"] or 0),
            "pattern_count": int(row["pattern_count"] or 0),
            "confirmed_memory_count": int(row["confirmed_memory_count"] or 0),
            "proposed_memory_count": int(row["proposed_memory_count"] or 0),
            "retrieval_mode": "agent_sidecar",
        }

    def import_text(
        self,
        *,
        title: str,
        content: str | bytes,
        kind: KnowledgeKind | str,
        scope: str,
        authority: str,
        vendor: str | None = None,
        product_family: str | None = None,
        version: str | None = None,
        language: str | None = None,
        status: KnowledgeStatus | str | None = None,
        locator: str | None = None,
        revision: str = "1",
        supersedes: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        source_sha256: str | None = None,
        external_key: str | None = None,
    ) -> KnowledgeSource:
        decoded = _decode_text(content, "content")
        if len(decoded) > _MAX_SOURCE_CHARS:
            raise ValueError("knowledge source is larger than the 25 million character limit")
        return self._insert_source(
            title=title,
            source_payload=decoded.encode("utf-8"),
            kind=kind,
            scope=scope,
            authority=authority,
            vendor=vendor,
            product_family=product_family,
            version=version,
            language=language,
            status=status,
            locator=locator,
            revision=revision,
            supersedes=supersedes,
            metadata=metadata,
            source_sha256=source_sha256,
            external_key=external_key,
            payload_format="text",
        )

    def import_pages(
        self,
        *,
        title: str,
        pages: Iterable[PageRecord | Mapping[str, Any]],
        kind: KnowledgeKind | str,
        scope: str,
        authority: str,
        vendor: str | None = None,
        product_family: str | None = None,
        version: str | None = None,
        language: str | None = None,
        status: KnowledgeStatus | str | None = None,
        locator: str | None = None,
        revision: str = "1",
        supersedes: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        source_sha256: str | None = None,
        external_key: str | None = None,
    ) -> KnowledgeSource:
        normalized_pages: list[PageRecord] = []
        for item in pages:
            if isinstance(item, PageRecord):
                page_record = item
            elif isinstance(item, Mapping):
                page_record = PageRecord(
                    text=item.get("text", ""),
                    page=item.get("page"),
                    locator=item.get("locator"),
                    section=item.get("section"),
                    metadata=item.get("metadata"),
                )
            else:
                raise TypeError("pages must contain PageRecord or mapping values")

            if page_record.page is not None and (
                not isinstance(page_record.page, int) or page_record.page < 1
            ):
                raise ValueError("page numbers must be positive integers")
            normalized_pages.append(page_record)
            if len(normalized_pages) > 100_000:
                raise ValueError("knowledge source contains too many page records")

        if not normalized_pages:
            raise ValueError("pages must not be empty")

        canonical_pages: list[dict[str, Any]] = []
        character_count = 0
        for page_record in normalized_pages:
            text = _decode_text(page_record.text, "page text")
            character_count += len(text)
            if character_count > _MAX_SOURCE_CHARS:
                raise ValueError("knowledge source is larger than the 25 million character limit")
            page_locator = _clean_optional(
                page_record.locator,
                "page locator",
                maximum=4_096,
            )
            section = _clean_optional(page_record.section, "page section")
            if section is None:
                section = _first_heading(text)
            page_metadata = _metadata(page_record.metadata)
            canonical_pages.append(
                {
                    "page": page_record.page,
                    "locator": page_locator,
                    "section": section,
                    "text": text,
                    "metadata": page_metadata,
                }
            )

        source_payload = _json_dump(canonical_pages).encode("utf-8")
        return self._insert_source(
            title=title,
            source_payload=source_payload,
            kind=kind,
            scope=scope,
            authority=authority,
            vendor=vendor,
            product_family=product_family,
            version=version,
            language=language,
            status=status,
            locator=locator,
            revision=revision,
            supersedes=supersedes,
            metadata=metadata,
            source_sha256=source_sha256,
            external_key=external_key,
            payload_format="pages-json",
        )

    def propose_memory(
        self,
        *,
        title: str,
        content: str | bytes,
        scope: str,
        proposed_by: str,
        kind: KnowledgeKind | str = KnowledgeKind.MEMORY,
        vendor: str | None = None,
        product_family: str | None = None,
        version: str | None = None,
        language: str | None = None,
        locator: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> KnowledgeSource:
        normalized_kind = _kind(kind)
        if normalized_kind not in {KnowledgeKind.MEMORY, KnowledgeKind.PREFERENCE}:
            raise ValueError("propose_memory only accepts memory or preference knowledge")
        return self.import_text(
            title=title,
            content=content,
            kind=normalized_kind,
            scope=scope,
            authority=f"user-proposed:{_clean_required(proposed_by, 'proposed_by')}",
            vendor=vendor,
            product_family=product_family,
            version=version,
            language=language,
            status=KnowledgeStatus.PROPOSED,
            locator=locator,
            metadata=metadata,
        )

    def confirm_memory(
        self,
        source_id: str,
        *,
        confirmed_by: str,
        expected_revision: str | None = None,
    ) -> KnowledgeSource:
        source_id = _clean_required(source_id, "source_id", maximum=64)
        actor = _clean_required(confirmed_by, "confirmed_by")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM knowledge_sources WHERE source_id = ?",
                    (source_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(source_id)
                if row["kind"] not in {
                    KnowledgeKind.MEMORY.value,
                    KnowledgeKind.PREFERENCE.value,
                }:
                    raise KnowledgeConflict(
                        "only proposed memory or preference sources can be confirmed"
                    )
                if row["status"] != KnowledgeStatus.PROPOSED.value:
                    raise KnowledgeConflict("memory confirmation requires current status PROPOSED")
                if expected_revision is not None and row["revision"] != expected_revision:
                    raise KnowledgeConflict("memory revision changed before confirmation")
                timestamp = _utc_text(_utc_now())
                connection.execute(
                    """
                    UPDATE knowledge_sources
                    SET status = ?, confirmed_by = ?, updated_at = ?
                    WHERE source_id = ?
                    """,
                    (
                        KnowledgeStatus.CONFIRMED.value,
                        actor,
                        timestamp,
                        source_id,
                    ),
                )
                if row["supersedes"] is not None:
                    connection.execute(
                        """
                        UPDATE knowledge_sources
                        SET status = ?, updated_at = ?
                        WHERE source_id = ?
                        """,
                        (
                            KnowledgeStatus.SUPERSEDED.value,
                            timestamp,
                            row["supersedes"],
                        ),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        confirmed = self.get_source(source_id)
        if confirmed is None:  # Guarded by the transaction above.
            raise KnowledgeError("confirmed memory disappeared")
        return confirmed

    def seed_control_patterns(
        self,
        seeds: Iterable[ControlPatternSeed],
    ) -> tuple[KnowledgeSource, ...]:
        """Idempotently load reviewed pattern records supplied by a separate package."""

        imported: list[KnowledgeSource] = []
        for seed in seeds:
            pattern_id = _clean_required(seed.pattern_id, "pattern_id", maximum=256)
            external_key = f"control-pattern:{pattern_id}"
            source_payload = _decode_text(seed.content, "pattern content").encode("utf-8")
            digest = hashlib.sha256(source_payload).hexdigest()
            existing = self.get_source_by_external_key(external_key)
            if (
                existing is not None
                and existing.sha256 == digest
                and existing.revision == seed.revision
                and existing.status == KnowledgeStatus.CONFIRMED
            ):
                imported.append(existing)
                continue
            if existing is not None:
                self.delete_source(existing.source_id)
            seed_metadata = _metadata(seed.metadata)
            seed_metadata["pattern_id"] = pattern_id
            try:
                imported_source = self.import_text(
                    title=seed.title,
                    content=seed.content,
                    kind=KnowledgeKind.CONTROL_PATTERN,
                    scope=seed.scope,
                    authority=seed.authority,
                    vendor=seed.vendor,
                    product_family=seed.product_family,
                    version=seed.version,
                    language=seed.language,
                    status=KnowledgeStatus.CONFIRMED,
                    revision=seed.revision,
                    metadata=seed_metadata,
                    external_key=external_key,
                )
            except KnowledgeConflict:
                # Two launchers may initialize the same database together. A
                # concurrent identical winner is success; incompatible content
                # remains a hard failure instead of being silently accepted.
                winner = self.get_source_by_external_key(external_key)
                if (
                    winner is None
                    or winner.sha256 != digest
                    or winner.revision != seed.revision
                    or winner.status != KnowledgeStatus.CONFIRMED
                ):
                    raise
                imported_source = winner
            imported.append(imported_source)
        return tuple(imported)

    def get_source(self, source_id: str) -> KnowledgeSource | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return None if row is None else self._source_from_row(row)

    def get_source_by_external_key(self, external_key: str) -> KnowledgeSource | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_sources WHERE external_key = ?",
                (external_key,),
            ).fetchone()
        return None if row is None else self._source_from_row(row)

    def list_sources(
        self,
        *,
        kinds: Iterable[KnowledgeKind | str] | None = None,
        statuses: Iterable[KnowledgeStatus | str] | None = None,
        limit: int = 100,
    ) -> tuple[KnowledgeSource, ...]:
        limit = min(max(int(limit), 1), 1_000)
        clauses: list[str] = []
        parameters: list[Any] = []
        normalized_kinds = self._normalize_kinds(kinds)
        normalized_statuses = self._normalize_statuses(statuses, default_confirmed=False)
        if normalized_kinds:
            placeholders = ",".join("?" for _ in normalized_kinds)
            clauses.append(f"kind IN ({placeholders})")
            parameters.extend(normalized_kinds)
        if normalized_statuses:
            placeholders = ",".join("?" for _ in normalized_statuses)
            clauses.append(f"status IN ({placeholders})")
            parameters.extend(normalized_statuses)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM knowledge_sources
                {where}
                ORDER BY updated_at DESC, source_id
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return tuple(self._source_from_row(row) for row in rows)

    def confirmed_sources_for_scope(self, *, scope: str, kinds) -> tuple[KnowledgeSource, ...]:
        """Exact project applicability; no global fallback or post-limit filtering."""
        normalized = sorted(self._normalize_kinds(kinds))
        if not normalized:
            return ()
        placeholders = ','.join('?' for _ in normalized)
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM knowledge_sources WHERE scope = ? AND status = ? AND kind IN ({placeholders}) ORDER BY source_id",
                (scope, KnowledgeStatus.CONFIRMED.value, *normalized)).fetchall()
        return tuple(self._source_from_row(row) for row in rows)

    def agent_source_snapshot(self) -> list[dict[str, Any]]:
        """Return an explicit sanitized source snapshot for Agent-owned RAG.

        Source payloads never leave the Platform verbatim: text, locators and
        metadata are redacted at this boundary.  Archived/unconfirmed sources
        are omitted so a restart, update, or delete replaces the Agent index.
        """

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT s.*, p.payload_format, p.payload
                FROM knowledge_sources AS s
                JOIN knowledge_source_payloads AS p ON p.source_id = s.source_id
                WHERE s.status = ?
                ORDER BY s.source_id
                """,
                (KnowledgeStatus.CONFIRMED.value,),
            ).fetchall()
        sources: list[dict[str, Any]] = []
        for row in rows:
            source_id = str(row["source_id"])
            payload = bytes(row["payload"])
            try:
                if str(row["payload_format"]) == "text":
                    pages = [{"text": payload.decode("utf-8")}]
                else:
                    decoded = json.loads(payload.decode("utf-8"))
                    pages = decoded if isinstance(decoded, list) else [{"text": ""}]
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise KnowledgeError("stored knowledge source payload is invalid")
            for page in pages:
                if not isinstance(page, dict) or not isinstance(page.get("text"), str):
                    raise KnowledgeError("stored knowledge page is invalid")
                page_number = page.get("page")
                section = page.get("section")
                locator = page.get("locator") or row["locator"]
                query: list[str] = []
                if isinstance(page_number, int):
                    query.append(f"page={page_number}")
                if isinstance(section, str) and section:
                    query.append(f"section={quote(_redact_sensitive_text(section), safe='')}")
                reference = f"knowledge://{source_id}"
                if query:
                    reference = f"{reference}?{'&'.join(query)}"
                metadata = _sanitize_metadata(_json_object(str(row["metadata_json"])))
                page_metadata = page.get("metadata")
                if isinstance(page_metadata, Mapping):
                    metadata.update(_sanitize_metadata(page_metadata))
                sources.append(
                    {
                        "reference": reference,
                        "source_id": source_id,
                        "source_sha256": str(row["sha256"]),
                        "source_revision": str(row["revision"]),
                        "content": _redact_sensitive_text(page["text"]),
                        "title": _redact_sensitive_text(str(row["title"])),
                        "kind": str(row["kind"]),
                        "scope": str(row["scope"]),
                        "vendor": row["vendor"],
                        "product_family": row["product_family"],
                        "version": row["version"],
                        "language": row["language"],
                        "authority": row["authority"],
                        "locator": (
                            _redact_sensitive_text(str(locator))
                            if locator is not None else None
                        ),
                        "page": page_number if isinstance(page_number, int) else None,
                        "section": (
                            _redact_sensitive_text(section)
                            if isinstance(section, str) else None
                        ),
                        "metadata": metadata,
                    }
                )
        return sources

    def delete_source(self, source_id: str) -> bool:
        """Archive a source while preserving prior citations and search audit.

        The public API retains its DELETE-shaped contract, but industrial
        evidence must remain reproducible after a user removes a source from
        active retrieval. Releasing ``external_key`` permits an intentional
        re-import or a revised built-in pattern without erasing the old chunks.
        """

        source_id = _clean_required(source_id, "source_id", maximum=64)
        with self._connection() as connection, connection:
            cursor = connection.execute(
                """
                UPDATE knowledge_sources
                SET status = ?, external_key = NULL, updated_at = ?
                WHERE source_id = ? AND status != ?
                """,
                (
                    KnowledgeStatus.ARCHIVED.value,
                    _utc_text(_utc_now()),
                    source_id,
                    KnowledgeStatus.ARCHIVED.value,
                ),
            )
        return cursor.rowcount > 0

    def _insert_source(
        self,
        *,
        title: str,
        source_payload: bytes,
        kind: KnowledgeKind | str,
        scope: str,
        authority: str,
        vendor: str | None,
        product_family: str | None,
        version: str | None,
        language: str | None,
        status: KnowledgeStatus | str | None,
        locator: str | None,
        revision: str,
        supersedes: str | None,
        metadata: Mapping[str, Any] | None,
        source_sha256: str | None,
        external_key: str | None,
        payload_format: str,
    ) -> KnowledgeSource:
        normalized_kind = _kind(kind)
        normalized_status = (
            KnowledgeStatus.PROPOSED
            if status is None
            and normalized_kind in {KnowledgeKind.MEMORY, KnowledgeKind.PREFERENCE}
            else _status(status or KnowledgeStatus.CONFIRMED)
        )
        if (
            normalized_kind in {KnowledgeKind.MEMORY, KnowledgeKind.PREFERENCE}
            and normalized_status == KnowledgeStatus.CONFIRMED
        ):
            raise KnowledgeConflict(
                "memory and preference sources must be proposed, then explicitly confirmed"
            )
        normalized_title = _redact_sensitive_text(_clean_required(title, "title"))
        normalized_scope = _clean_required(scope, "scope", maximum=256)
        normalized_authority = _clean_required(authority, "authority", maximum=256)
        normalized_vendor = _clean_optional(vendor, "vendor", maximum=128)
        if normalized_vendor is not None:
            normalized_vendor = _redact_sensitive_text(normalized_vendor)
        normalized_product = _clean_optional(
            product_family,
            "product_family",
            maximum=256,
        )
        if normalized_product is not None:
            normalized_product = _redact_sensitive_text(normalized_product)
        normalized_version = _clean_optional(version, "version", maximum=128)
        if normalized_version is not None:
            normalized_version = _redact_sensitive_text(normalized_version)
        normalized_language = _clean_optional(language, "language", maximum=64)
        normalized_locator = _clean_optional(locator, "locator", maximum=4_096)
        if normalized_locator is not None:
            normalized_locator = _redact_sensitive_text(normalized_locator)
        normalized_revision = _clean_required(revision, "revision", maximum=128)
        normalized_supersedes = _clean_optional(
            supersedes,
            "supersedes",
            maximum=64,
        )
        normalized_external_key = _clean_optional(
            external_key,
            "external_key",
            maximum=512,
        )
        normalized_metadata = _metadata(metadata)
        digest = _safe_sha256(source_sha256, source_payload)
        source_id = str(uuid.uuid4())
        timestamp = _utc_text(_utc_now())

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if normalized_external_key is not None:
                    existing = connection.execute(
                        "SELECT source_id FROM knowledge_sources WHERE external_key = ?",
                        (normalized_external_key,),
                    ).fetchone()
                    if existing is not None:
                        raise KnowledgeConflict(
                            f"knowledge external key already exists: {normalized_external_key}"
                        )
                if normalized_supersedes is not None:
                    previous = connection.execute(
                        "SELECT source_id FROM knowledge_sources WHERE source_id = ?",
                        (normalized_supersedes,),
                    ).fetchone()
                    if previous is None:
                        raise KnowledgeConflict("superseded source does not exist")

                connection.execute(
                    """
                    INSERT INTO knowledge_sources(
                        source_id, external_key, kind, title, scope,
                        vendor, vendor_key, product_family, version, language,
                        authority, status, sha256, locator, revision, supersedes,
                        metadata_json, confirmed_by, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                    """,
                    (
                        source_id,
                        normalized_external_key,
                        normalized_kind.value,
                        normalized_title,
                        normalized_scope,
                        normalized_vendor,
                        (
                            _normalize_vendor(normalized_vendor)
                            if normalized_vendor is not None
                            else None
                        ),
                        normalized_product,
                        normalized_version,
                        normalized_language,
                        normalized_authority,
                        normalized_status.value,
                        digest,
                        normalized_locator,
                        normalized_revision,
                        normalized_supersedes,
                        _json_dump(normalized_metadata),
                        timestamp,
                        timestamp,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO knowledge_source_payloads(
                        source_id, payload_format, payload, payload_sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        source_id,
                        payload_format,
                        source_payload,
                        hashlib.sha256(source_payload).hexdigest(),
                        timestamp,
                    ),
                )
                if (
                    normalized_supersedes is not None
                    and normalized_status == KnowledgeStatus.CONFIRMED
                ):
                    connection.execute(
                        """
                        UPDATE knowledge_sources
                        SET status = ?, updated_at = ?
                        WHERE source_id = ?
                        """,
                        (
                            KnowledgeStatus.SUPERSEDED.value,
                            timestamp,
                            normalized_supersedes,
                        ),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

        source = self.get_source(source_id)
        if source is None:  # Guarded by successful commit.
            raise KnowledgeError("imported source disappeared")
        return source

    @staticmethod
    def _normalize_kinds(
        values: Iterable[KnowledgeKind | str] | None,
    ) -> tuple[str, ...]:
        if values is None:
            return ()
        return tuple(dict.fromkeys(_kind(value).value for value in values))

    @staticmethod
    def _normalize_statuses(
        values: Iterable[KnowledgeStatus | str] | None,
        *,
        default_confirmed: bool,
    ) -> tuple[str, ...]:
        if values is None:
            return (KnowledgeStatus.CONFIRMED.value,) if default_confirmed else ()
        return tuple(dict.fromkeys(_status(value).value for value in values))

    @staticmethod
    def _source_from_row(row: sqlite3.Row) -> KnowledgeSource:
        return KnowledgeSource(
            source_id=str(row["source_id"]),
            external_key=row["external_key"],
            kind=KnowledgeKind(str(row["kind"])),
            title=str(row["title"]),
            scope=str(row["scope"]),
            vendor=row["vendor"],
            product_family=row["product_family"],
            version=row["version"],
            language=row["language"],
            authority=str(row["authority"]),
            status=KnowledgeStatus(str(row["status"])),
            sha256=str(row["sha256"]),
            locator=row["locator"],
            revision=str(row["revision"]),
            supersedes=row["supersedes"],
            metadata=_json_object(str(row["metadata_json"])),
            confirmed_by=row["confirmed_by"],
            created_at=_parse_utc(str(row["created_at"])),
            updated_at=_parse_utc(str(row["updated_at"])),
        )
