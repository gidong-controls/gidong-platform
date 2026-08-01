from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from controlstudio_host.knowledge import KnowledgeKind, KnowledgeStore, PageRecord


def test_legacy_chunk_database_migrates_to_durable_source_payload_without_fts(
    tmp_path: Path,
) -> None:
    database = tmp_path / "knowledge.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE knowledge_schema (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
            INSERT INTO knowledge_schema VALUES (1, '2026-01-01T00:00:00+00:00');
            CREATE TABLE knowledge_sources (
                source_id TEXT PRIMARY KEY, external_key TEXT, kind TEXT NOT NULL,
                title TEXT NOT NULL, scope TEXT NOT NULL, vendor TEXT, vendor_key TEXT,
                product_family TEXT, version TEXT, language TEXT, authority TEXT NOT NULL,
                status TEXT NOT NULL, sha256 TEXT NOT NULL, locator TEXT, revision TEXT NOT NULL,
                supersedes TEXT, metadata_json TEXT NOT NULL, confirmed_by TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE knowledge_chunks (
                chunk_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                content TEXT NOT NULL, sha256 TEXT NOT NULL, locator TEXT, page INTEGER,
                section TEXT, metadata_json TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """INSERT INTO knowledge_sources VALUES
            ('legacy-source', NULL, 'manual', 'Legacy manual', 'vendor:ls', 'ls', 'ls',
             'XGK', 'V2.5', 'en', 'user', 'confirmed', ?, 'manual.pdf', '1', NULL,
             '{}', NULL, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')""",
            ("a" * 64,),
        )
        connection.execute(
            """INSERT INTO knowledge_chunks VALUES
            ('legacy-chunk', 'legacy-source', 0, 'timer evidence', ?, 'manual.pdf#page=4',
             4, 'Timers', '{}')""",
            ("b" * 64,),
        )

    store = KnowledgeStore(database)
    snapshot = store.agent_source_snapshot()

    assert snapshot[0]["reference"] == "knowledge://legacy-source?page=4&section=Timers"
    assert snapshot[0]["content"] == "timer evidence"
    with sqlite3.connect(database) as connection:
        payload = connection.execute(
            "SELECT payload_format, payload FROM knowledge_source_payloads"
        ).fetchone()
        assert payload is not None
        assert payload[0] == "legacy-chunk-reconstruction"
        assert json.loads(payload[1])[0]["text"] == "timer evidence"
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'knowledge_fts_word'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'knowledge_chunks'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'knowledge_search_audit'"
        ).fetchone() is None


def test_platform_snapshot_is_sanitized_and_delete_survives_restart(tmp_path: Path) -> None:
    database = tmp_path / "knowledge.sqlite3"
    store = KnowledgeStore(database)
    source = store.import_pages(
        title="Private commissioning note",
        pages=[
            PageRecord(
                page=2,
                section="Safety",
                locator="private.pdf#page=2",
                text="password=do-not-send; verify the permissive before motion.",
            )
        ],
        kind=KnowledgeKind.PROJECT_DOCUMENT,
        scope="workspace:line-7",
        authority="user",
        vendor="ls",
    )
    document = store.agent_source_snapshot()[0]
    assert document["reference"].startswith(f"knowledge://{source.source_id}?page=2")
    assert "do-not-send" not in document["content"]
    assert store.delete_source(source.source_id)

    restarted = KnowledgeStore(database)
    assert restarted.agent_source_snapshot() == []
    assert restarted.get_source(source.source_id) is not None
